"""Corpus boundary, offline embedding and context checks without generation calls."""

from copy import deepcopy
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from incidentclue.rag import RetrievalError
from incidentclue.rag.__main__ import main
from incidentclue.rag.corpus import RetrievalConfig, canonical, load_corpus
from incidentclue.rag.embeddings import LocalEmbedder, artifact_contract, checked_vector
from incidentclue.rag.storage import bound_sources

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = artifact_contract()
CONFIG = RetrievalConfig(max_distance=0.4)


@pytest.fixture
def corpus_root(tmp_path):
    shutil.copytree(ROOT / "data/runbooks", tmp_path / "data/runbooks")
    return tmp_path


def change_manifest(root, change):
    path = root / "data/runbooks/manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def change_document(root, text):
    path = root / "data/runbooks/configuration.md"
    path.write_text(text, encoding="utf-8")
    change_manifest(root, lambda m: m["documents"][0].update(sha256=sha256(path.read_bytes()).hexdigest()))


def test_runtime_chunks_match_frozen_foundation_without_evaluator_imports():
    corpus = load_corpus(ROOT, ARTIFACT, CONFIG)
    assert len(corpus.passages) == 36
    assert len(corpus.contract["manifest"]["documents"]) == 12
    # The runtime does not need labels/cases/rubrics; a corpus-only root works below.
    for passage in corpus.passages:
        lines = (ROOT / passage["path"]).read_text(encoding="utf-8").splitlines(keepends=True)
        assert "".join(lines[passage["line_start"] - 1:passage["line_end"]]).rstrip() == passage["text"]


def test_identity_is_stable_and_includes_artifact_config_and_source(corpus_root):
    original = load_corpus(corpus_root, ARTIFACT, CONFIG)
    assert original == load_corpus(corpus_root, ARTIFACT, CONFIG)
    assert load_corpus(corpus_root, ARTIFACT, RetrievalConfig(max_distance=0.5)).corpus_id != original.corpus_id
    different = deepcopy(ARTIFACT)
    different["revision"] = "another-revision"
    assert load_corpus(corpus_root, different, CONFIG).corpus_id != original.corpus_id
    change_document(corpus_root, "# Configuration\n\n## Procedure\nCheck the recorded configuration.\n")
    assert load_corpus(corpus_root, ARTIFACT, CONFIG).corpus_id != original.corpus_id
    # Frozen objects return independent JSON copies.
    original.passages.clear()
    assert len(original.passages) == 36


@pytest.mark.parametrize("change", [
    lambda m: m["documents"][0].update(path="evals/rag/labels.json"),
    lambda m: m["documents"][0].update(path="data/runbooks/../secret.md"),
    lambda m: m["documents"][0].update(sha256="0" * 64),
    lambda m: m["documents"][0].update(effective_date="yesterday"),
    lambda m: m["documents"][0].update(services=[]),
    lambda m: m["documents"][0].update(services=["billing", "billing"]),
    lambda m: m["documents"][0].update(status="superseded", superseded_by="absent"),
    lambda m: m["documents"].append(m["documents"][0]),
    lambda m: m.update(chunk_policy="recursive-repository"),
    lambda m: m.update(unrecognized="ignored?"),
])
def test_invalid_allowlist_and_metadata_refused(corpus_root, change):
    change_manifest(corpus_root, change)
    with pytest.raises(RetrievalError, match="Corpus validation"):
        load_corpus(corpus_root, ARTIFACT, CONFIG)


@pytest.mark.parametrize("text", [
    "No headings", "## \nText", "## Same\nOne\n## Same\nTwo", "## Procedure\n" + "x" * 1001,
    "## Procedure\n" + "\u754c" * 334,
])
def test_missing_duplicate_and_oversize_sections_refused(corpus_root, text):
    change_document(corpus_root, text)
    with pytest.raises(RetrievalError):
        load_corpus(corpus_root, ARTIFACT, CONFIG)


def test_linked_corpus_path_refused(corpus_root, tmp_path):
    source = corpus_root / "data/runbooks/configuration.md"
    destination = tmp_path / "outside.md"
    source.rename(destination)
    try:
        source.symlink_to(destination)
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer privileges.")
    with pytest.raises(RetrievalError):
        load_corpus(corpus_root, ARTIFACT, CONFIG)


@pytest.mark.parametrize("vector", [[], [0.0] * 384, [float("nan")] * 384,
                                    [float("inf")] * 384, [1e100] * 384, [1e-100] * 384])
def test_bad_embedding_vectors_refused(vector):
    with pytest.raises(RetrievalError):
        checked_vector(vector)


def test_missing_or_corrupt_artifacts_fail_before_backend_load(tmp_path, monkeypatch):
    import sys
    fake = SimpleNamespace(TextEmbedding=lambda **kwargs: pytest.fail("Loaded unverified artifacts"))
    monkeypatch.setitem(sys.modules, "fastembed", fake)
    with pytest.raises(RetrievalError, match="Local embedding setup failed"):
        LocalEmbedder(tmp_path)
    (tmp_path / "config.json").write_text("corrupt", encoding="utf-8")
    with pytest.raises(RetrievalError):
        LocalEmbedder(tmp_path)


def fake_local(**methods):
    embedder = object.__new__(LocalEmbedder)
    embedder.artifact = ARTIFACT
    embedder.model = SimpleNamespace(**methods)
    return embedder


def test_token_limit_refuses_before_embedding_and_never_truncates():
    model = fake_local(token_count=lambda text: 201,
                       passage_embed=lambda text: pytest.fail("Truncated an oversize passage"),
                       query_embed=lambda text: pytest.fail("Truncated an oversize query"))
    with pytest.raises(RetrievalError, match="200-token"):
        model.passage_vectors(["text"])
    with pytest.raises(RetrievalError, match="200-token"):
        model.query_vector("text")


def test_backend_failures_and_wrong_counts_are_controlled():
    model = fake_local(token_count=lambda text: 5, passage_embed=lambda texts: iter([]),
                       query_embed=lambda text: iter([]))
    with pytest.raises(RetrievalError, match="count"):
        model.passage_vectors(["text"])
    with pytest.raises(RetrievalError, match="exactly one"):
        model.query_vector("text")
    model.model.query_embed = lambda text: (_ for _ in ()).throw(RuntimeError("private-file-path"))
    with pytest.raises(RetrievalError, match="Local query embedding failed"):
        model.query_vector("text")


def test_bounded_sources_keep_exact_versions_and_utf8_budgets():
    corpus = load_corpus(ROOT, ARTIFACT, CONFIG)
    candidates = [{"metadata": passage, "score": 0.1} for passage in corpus.passages]
    result = bound_sources(corpus.corpus_id, candidates, CONFIG.model_dump(), mode="vector")
    sources = result["retrieved_sources"]
    assert 0 < len(sources) <= 3
    assert len(canonical(sources).encode("utf-8")) <= 4000
    assert sum(len(s["text"].encode("utf-8")) for s in sources) <= 3000
    assert result["bounds_excluded"] == len(candidates) - len(sources)
    assert [s["rank"] for s in sources] == list(range(1, len(sources) + 1))
    for source in sources:
        passage = next(p for p in corpus.passages if source["id"].endswith(":" + p["passage_id"]))
        assert source["text"] == passage["text"]
        assert source["document_sha256"] == passage["document_sha256"]
        assert source["corpus_id"] == corpus.corpus_id
    with pytest.raises(RetrievalError, match="duplicate"):
        bound_sources(corpus.corpus_id, [candidates[0], candidates[0]], CONFIG.model_dump(), mode="vector")


def test_bounded_context_accounts_for_multibyte_metadata():
    corpus = load_corpus(ROOT, ARTIFACT, CONFIG)
    candidates = []
    for passage in corpus.passages[:3]:
        passage["title"] = "\u754c" * 120
        passage["text"] = "\u754c" * 333
        candidates.append({"metadata": passage, "score": 0.1})
    result = bound_sources(corpus.corpus_id, candidates, CONFIG.model_dump(), mode="lexical")
    assert 0 < len(result["retrieved_sources"]) < 3
    assert result["bounds_excluded"] == 3 - len(result["retrieved_sources"])
    assert result["context_bytes"] <= 4000


def test_admin_validate_is_provider_database_and_embedding_free(corpus_root, monkeypatch, capsys):
    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda *a, **kw: pytest.fail("Connected during validation"))
    monkeypatch.setenv("GROQ_API_KEY", "must-not-appear")
    assert main(["validate", "--root", str(corpus_root), "--max-distance", "0.4"]) == 0
    output = capsys.readouterr()
    assert "must-not-appear" not in output.out + output.err
    assert json.loads(output.out)["embedding_tokens_checked"] is False


def test_admin_missing_storage_and_driver_failure_are_safe(monkeypatch, capsys):
    import psycopg
    monkeypatch.delenv("INCIDENTCLUE_DATABASE_URL", raising=False)
    assert main(["setup"]) == 1
    monkeypatch.setenv("INCIDENTCLUE_DATABASE_URL", "postgresql://private-password@invalid")
    def fail(*args, **kwargs):
        raise RuntimeError("private-password")
    monkeypatch.setattr(psycopg, "connect", fail)
    assert main(["setup"]) == 1
    assert "private-password" not in capsys.readouterr().err


class PreflightConnection:
    """Scripted read results; any attempted mutation is an immediate failure."""
    def __init__(self, results):
        self.results = iter(results)
        self.statements = []

    def execute(self, statement, parameters=None):
        self.statements.append(statement)
        assert statement.strip().startswith("SELECT"), "Read preflight attempted a write"
        result = next(self.results)
        return SimpleNamespace(fetchone=lambda: result, fetchall=lambda: result)

    @contextmanager
    def transaction(self):
        yield


@pytest.mark.parametrize("results", [
    [{"server": 170004, "vector": None, "namespace": None}],
    [{"server": 160004, "vector": "0.8.1", "namespace": "public"}],
    [{"server": 170004, "vector": "0.7.0", "namespace": "public"}],
    [{"server": 170004, "vector": "0.8.1", "namespace": "elsewhere"}],
    [{"server": 170004, "vector": "0.8.1", "namespace": "public"},
     {"version": None, "corpora": None, "passages": None}],
    [{"server": 170004, "vector": "0.8.1", "namespace": "public"},
     {"version": "schema_version", "corpora": "corpora", "passages": "passages"},
     [{"version": 999}]],
])
def test_incompatible_or_missing_storage_preflight_never_writes(results):
    from incidentclue.rag.storage import require_schema
    connection = PreflightConnection(results)
    with pytest.raises(RetrievalError):
        require_schema(connection)


def test_inspection_of_missing_corpus_is_failure_without_setup():
    from incidentclue.rag.storage import inspect_corpus
    connection = PreflightConnection([
        {"server": 170004, "vector": None, "namespace": None}])
    with pytest.raises(RetrievalError, match="explicit setup"):
        inspect_corpus(connection, "0" * 64)
