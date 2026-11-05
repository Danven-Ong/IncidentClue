"""Real pgvector checks, only with a separately configured isolated RAG test DB."""

from copy import deepcopy
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from incidentclue.rag import RetrievalError
from incidentclue.rag.corpus import Corpus, RetrievalConfig, canonical, identity, load_corpus
from incidentclue.rag.embeddings import artifact_contract
from incidentclue.rag.storage import ingest, inspect_corpus, lookup, prepare_schema, require_schema

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.postgres


class Embedder:
    artifact = artifact_contract()
    calls = 0

    def passage_vectors(self, texts):
        self.calls += 1
        return [[1.0] + [0.0] * 383 for _ in texts]

    def query_vector(self, text):
        self.calls += 1
        return [1.0] + [0.0] * 383


@pytest.fixture
def connection():
    url = os.environ.get("INCIDENTCLUE_RAG_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set INCIDENTCLUE_RAG_TEST_DATABASE_URL to an isolated PG17/vector0.8.1 database.")
    pytest.importorskip("pgvector")
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as connection:
        prepare_schema(connection)
        yield connection


@pytest.fixture
def corpus():
    base = load_corpus(ROOT, Embedder.artifact, RetrievalConfig(max_distance=0.4))
    contract = base.contract
    # Independent synthetic source versions allow concurrent/retained test data.
    # No existing corpus is deleted by these tests.
    contract["test_instance"] = str(uuid4())
    return Corpus(identity(contract), canonical(contract), base.passages_json)


def test_atomic_repeat_ingestion_and_inspection_without_embedder(connection, corpus):
    model = Embedder()
    assert ingest(connection, corpus, model)["published"] is True
    before = inspect_corpus(connection, corpus.corpus_id)
    assert ingest(connection, corpus, model)["published"] is False
    assert model.calls == 1
    assert before == inspect_corpus(connection, corpus.corpus_id)
    prepare_schema(connection)
    assert before == inspect_corpus(connection, corpus.corpus_id)


def test_embedding_partial_failure_publishes_nothing(connection, corpus):
    class Broken(Embedder):
        def passage_vectors(self, texts):
            yield [1.0] * 384
            raise RetrievalError("Interrupted embedding build")
    with pytest.raises(RetrievalError):
        ingest(connection, corpus, Broken())
    assert connection.execute("SELECT 1 FROM ic_rag.corpora WHERE corpus_id=%s", (corpus.corpus_id,)).fetchone() is None


def test_sql_failure_rolls_back_header_and_all_passages(connection, corpus):
    passages = corpus.passages
    passages[1]["passage_id"] = passages[0]["passage_id"]
    contract = corpus.contract
    contract["passages_sha256"] = identity(passages)
    broken = Corpus(identity(contract), canonical(contract), canonical(passages))
    with pytest.raises(psycopg.errors.UniqueViolation):
        ingest(connection, broken, Embedder())
    assert connection.execute("SELECT 1 FROM ic_rag.corpora WHERE corpus_id=%s", (broken.corpus_id,)).fetchone() is None


def test_exact_search_scope_supersession_and_deterministic_ties(connection, corpus):
    model = Embedder()
    ingest(connection, corpus, model)
    result = lookup(connection, corpus.corpus_id, "billing", "Resolver failed", embedder=model)
    again = lookup(connection, corpus.corpus_id, "billing", "Resolver failed", embedder=model)
    assert result == again
    eligible = [p for p in corpus.passages if p["status"] == "current" and "global" in p["services"]]
    assert result["eligible_passages"] == len(eligible)
    assert result["match_excluded"] == 0
    assert result["scope_excluded"] == len(corpus.passages) - len(eligible)
    assert [s["id"].rsplit(":", 1)[1] for s in result["retrieved_sources"]] == [p["passage_id"] for p in eligible[:3]]
    assert all("dns-legacy" not in s["id"] and "catalog-" not in s["id"] for s in result["retrieved_sources"])


def test_no_match_cutoff_and_lexical_do_not_conflate_failure(connection, corpus):
    model = Embedder()
    ingest(connection, corpus, model)
    result = lookup(connection, corpus.corpus_id, "billing", "zzzxxyyqq", mode="lexical")
    assert result["retrieved_sources"] == []
    lexical = lookup(connection, corpus.corpus_id, "billing", "resolver", mode="lexical")
    assert lexical["retrieved_sources"]
    assert all(s["distance"] is None and s["lexical_score"] > 0 for s in lexical["retrieved_sources"])
    class Opposite(Embedder):
        def query_vector(self, text):
            return [-1.0] + [0.0] * 383
    result = lookup(connection, corpus.corpus_id, "billing", "resolver", embedder=Opposite())
    assert result["retrieved_sources"] == []
    with pytest.raises(RetrievalError, match="unavailable"):
        lookup(connection, "0" * 64, "billing", "resolver", mode="lexical")
    with pytest.raises(RetrievalError, match="pinned local"):
        lookup(connection, corpus.corpus_id, "billing", "resolver")


def test_old_versions_remain_available_after_changed_config(connection, corpus):
    ingest(connection, corpus, Embedder())
    contract = deepcopy(corpus.contract)
    contract["retrieval"]["max_distance"] = 0.5
    newer = Corpus(identity(contract), canonical(contract), corpus.passages_json)
    ingest(connection, newer, Embedder())
    assert inspect_corpus(connection, corpus.corpus_id)["contract"] == corpus.contract
    assert inspect_corpus(connection, newer.corpus_id)["contract"] == newer.contract


def test_incompatible_setup_and_read_preflight_never_upgrade(connection):
    # Roll back this deliberate corruption, including on assertion failure.
    with connection.transaction(force_rollback=True):
        connection.execute("UPDATE ic_rag.schema_version SET version=999")
        with pytest.raises(RetrievalError, match="incompatible"):
            require_schema(connection)
        with pytest.raises(RetrievalError, match="incompatible"):
            prepare_schema(connection)
        assert connection.execute("SELECT version FROM ic_rag.schema_version").fetchone()["version"] == 999
