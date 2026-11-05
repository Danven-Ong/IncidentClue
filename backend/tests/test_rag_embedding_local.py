"""Optional real CPU artifact check; no downloads, generation or quality claims."""

import os
from pathlib import Path
import socket

import pytest

from incidentclue.rag.corpus import RetrievalConfig, load_corpus
from incidentclue.rag.embeddings import LocalEmbedder, artifact_contract, checked_vector

ROOT = Path(__file__).resolve().parents[2]


def test_verified_local_cpu_artifact_with_network_disabled(monkeypatch):
    directory = os.environ.get("INCIDENTCLUE_RAG_MODEL_DIR")
    if not directory:
        pytest.skip("Set INCIDENTCLUE_RAG_MODEL_DIR and install optional pinned RAG dependencies.")

    def blocked(*args, **kwargs):
        pytest.fail("Local embedding attempted network access")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    corpus = load_corpus(ROOT, artifact_contract(), RetrievalConfig(max_distance=0.4))
    embedder = LocalEmbedder(Path(directory))
    texts = [p["text"] for p in corpus.passages]
    vectors = embedder.passage_vectors(texts)
    assert len(vectors) == 36
    assert max(embedder.model.token_count(text) for text in texts) <= 200
    assert all(checked_vector(vector) == vector for vector in vectors)
    assert len(embedder.query_vector("Connection acquisition waits at the pool limit")) == 384
