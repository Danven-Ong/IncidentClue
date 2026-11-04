"""Candidate representation and control integrity, without external models."""

from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import os
from uuid import uuid4

import pytest

from incidentclue.rag.corpus import RetrievalConfig, load_corpus
from incidentclue.rag.embeddings import artifact_contract

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("representation_eval", ROOT / "evals/rag/representation.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_title_embedding_adds_only_title_and_preserves_source_text():
    passage = {"text": "## Checks\nRead configuration.", "title": "Required configuration",
               "id": "never-embed-id", "path": "never-embed-path", "label": "never-embed-label"}
    original = deepcopy(passage)
    candidate = evaluation.representation_inputs([passage], "title_context")
    assert candidate[passage["text"]] == "Required configuration\n## Checks\nRead configuration."
    assert passage == original
    assert evaluation.representation_inputs([passage], "body")[passage["text"]] == passage["text"]


def test_representation_identity_changes_without_model_or_document_changes():
    base = artifact_contract()
    alternative = evaluation.representation_artifact("title_context")
    assert alternative["files"] == base["files"] and alternative["revision"] == base["revision"]
    assert alternative != base
    assert evaluation.representation_artifact("body") == base
    first = load_corpus(ROOT, base, RetrievalConfig(max_distance=0.4))
    second = load_corpus(ROOT, alternative, RetrievalConfig(max_distance=0.4))
    assert first.corpus_id != second.corpus_id and first.passages == second.passages


def test_conflicting_titles_and_oversized_embedding_input_are_rejected():
    with pytest.raises(ValueError, match="Ambiguous"):
        evaluation.representation_inputs([{"title":"one","text":"same"},
                                           {"title":"two","text":"same"}], "title_context")
    with pytest.raises(ValueError, match="byte limit"):
        evaluation.representation_inputs([{"title":"\u754c"*700,"text":"body"}], "title_context")


def test_original_and_candidate_embeddings_cache_separately_but_share_query_vectors():
    calls = []
    backend = SimpleNamespace(artifact=artifact_contract(),
        passage_vectors=lambda texts: calls.append(("passage", texts)) or [[1.0] for _ in texts],
        query_vector=lambda query: calls.append(("query", query)) or [1.0])
    shared = evaluation.baseline.CachedEmbedder(backend)
    passages = [{"title":"Title","text":"body"}]
    body = evaluation.RepresentationEmbedder(shared, passages, "body")
    title = evaluation.RepresentationEmbedder(shared, passages, "title_context")
    for _ in range(2):
        body.passage_vectors(["body"])
        title.passage_vectors(["body"])
        body.query_vector("same query")
        title.query_vector("same query")
    assert calls == [("passage",["body"]),("passage",["Title\nbody"]),("query","same query")]


def row(sources):
    return {"case_id":"case-01","mode":"vector","cutoff":0.4,
            "result":{"retrieved_sources":sources}}


def test_control_comparison_accepts_tiny_numeric_noise_and_refuses_rank_changes():
    original = row([{"id":"source:a","distance":0.2}])
    previous = {("case-01","vector",0.4):original}
    evaluation.compare_control(row([{"id":"source:a","distance":0.2000001}]), previous)
    with pytest.raises(ValueError, match="ranks"):
        evaluation.compare_control(row([{"id":"source:b","distance":0.2}]), previous)
    with pytest.raises(ValueError, match="distances"):
        evaluation.compare_control(row([{"id":"source:a","distance":0.3}]), previous)


def test_protocol_is_bounded_and_prior_report_is_immutable(tmp_path):
    assert evaluation.protocol()["maximum_searches"] == 312
    path = tmp_path / "wrong.jsonl"
    path.write_text('{"type":"summary"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="identity mismatch"):
        evaluation.prior_control(path)


@pytest.mark.postgres
def test_real_lexical_or_handles_long_queries_and_punctuation_without_sql_execution():
    url = os.environ.get("INCIDENTCLUE_RAG_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set isolated INCIDENTCLUE_RAG_TEST_DATABASE_URL for lexical OR SQL checks")
    pytest.importorskip("pgvector")
    import psycopg
    from psycopg.rows import dict_row
    from incidentclue.rag.corpus import Corpus, canonical, identity
    from test_rag_postgres import Embedder
    parameters = evaluation.baseline.connection_parameters(url)
    with psycopg.connect(**parameters, autocommit=True, row_factory=dict_row) as connection:
        evaluation.prepare_schema(connection)
        base = load_corpus(ROOT, artifact_contract(), RetrievalConfig(max_distance=0.123))
        contract = base.contract
        contract["test_instance"] = str(uuid4())
        corpus = Corpus(identity(contract), canonical(contract), base.passages_json)
        evaluation.ingest(connection, corpus, Embedder())
        query = "required configuration setting zzznonexistenttoken"
        old = evaluation.lookup(connection, corpus.corpus_id, "billing", query, mode="lexical")
        new = evaluation.lexical_or(connection, corpus.corpus_id, "billing", query, contract["retrieval"])
        assert old["retrieved_sources"] == [] and new["retrieved_sources"]
        empty = evaluation.lexical_or(connection, corpus.corpus_id, "billing", "the and or", contract["retrieval"])
        assert empty["retrieved_sources"] == []
        evaluation.lexical_or(connection, corpus.corpus_id, "billing",
            "stock_endpoint') ; DROP SCHEMA ic_rag CASCADE; --", contract["retrieval"])
        assert connection.execute("SELECT to_regclass('ic_rag.corpora') AS table_name").fetchone()["table_name"]
