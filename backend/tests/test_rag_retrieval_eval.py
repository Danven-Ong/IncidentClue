"""Retrieval metric definitions, frozen-data boundary and deterministic selection."""

import importlib.util
import math
from pathlib import Path
import shutil
import socket

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("retrieval_eval", ROOT / "evals/rag/retrieval.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_graded_ranking_metrics_and_empty_slots():
    result = evaluation.metrics(["b", "unrelated", "a"], {"a": 2, "b": 1})
    assert result["recall_at_3"] == 1 and result["precision_at_3"] == 2 / 3
    assert result["mrr_at_3"] == 1
    assert result["ndcg_at_3"] == pytest.approx(2.5 / (3 + 1 / math.log2(3)))
    empty = evaluation.metrics([], {"a": 2})
    assert empty["recall_at_3"] == empty["precision_at_3"] == empty["mrr_at_3"] == 0
    assert evaluation.metrics(["a"], {"a": 2})["precision_at_3"] == 1 / 3


def test_empty_relevance_is_undefined_and_duplicate_ids_rejected():
    result = evaluation.metrics(["unrelated"], {})
    assert result["recall_at_3"] is result["mrr_at_3"] is result["ndcg_at_3"] is None
    assert result["precision_at_3"] == 0
    with pytest.raises(ValueError):
        evaluation.metrics(["a", "a"], {"a": 2})


def candidate(**updates):
    return {"assessed": 12, "answerable_cases": 6, "no_guidance_cases": 2,
            "answerable_hits": 6, "no_guidance_false_positives": 0,
            "scope_version_violations": 0, "mrr_at_3": 0.9,
            "ndcg_at_3": 0.8, "precision_at_3": 0.5, **updates}


@pytest.mark.parametrize("update", [
    {"no_guidance_false_positives": 1}, {"answerable_hits": 4},
    {"scope_version_violations": 1}, {"mrr_at_3": 0.4}, {"assessed": 11},
])
def test_selection_retains_failures_instead_of_relaxing_gates(update):
    assert evaluation.select_candidate({"0.4": candidate(**update)}, {"mrr_at_3": 0.5}) is None


def test_selection_is_deterministic_and_excludes_diagnostic_upper_bound():
    data = {"0.4": candidate(), "0.3": candidate(), "2.0": candidate(ndcg_at_3=1)}
    assert evaluation.select_candidate(data, {"mrr_at_3": 0.5}) == 0.3
    assert evaluation.select_candidate({"2.0": candidate()}, {"mrr_at_3": 0.5}) is None


def test_development_query_inputs_never_load_labels_or_held_out_snapshots(monkeypatch):
    original = evaluation.read
    paths = []
    def guarded(path):
        paths.append(path)
        assert path.name != "labels.json"
        assert path.name not in {f"case-{n:02}.json" for n in range(13, 25)}
        return original(path)
    monkeypatch.setattr(evaluation, "read", guarded)
    chosen = evaluation.development_inputs()
    assert len(chosen) == 12
    assert {item[0] for item in chosen} == {f"case-{n:02}" for n in range(1, 13)}


def test_frozen_fixture_changes_fail_before_evaluation(tmp_path):
    shutil.copytree(ROOT / "data/runbooks", tmp_path / "data/runbooks")
    shutil.copytree(ROOT / "evals/rag", tmp_path / "evals/rag")
    assert evaluation.verify_frozen(tmp_path) == evaluation.verify_frozen()
    (tmp_path / "data/runbooks/dns.md").write_text("Changed", encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen fixtures changed"):
        evaluation.verify_frozen(tmp_path)


def test_existing_output_cannot_be_replaced_before_model_load(tmp_path, monkeypatch):
    path = tmp_path / "existing.jsonl"
    path.write_text("original", encoding="utf-8")
    monkeypatch.setattr(evaluation, "LocalEmbedder", lambda *a: pytest.fail("Loaded model before exclusive output"))
    with pytest.raises(FileExistsError):
        evaluation.run(None, tmp_path, path)
    assert path.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize("url", ["", "host=remote port=5432 dbname=test", "host=127.0.0.1 hostaddr=8.8.8.8 port=5432 dbname=test", "host=127.0.0.1 port=5432 service=elsewhere dbname=test"])
def test_connection_rejects_external_and_implicit_database_before_libpq_connect(url):
    with pytest.raises(ValueError):
        evaluation.connection_parameters(url)


def test_network_guard_refuses_dns_and_connections_to_external_hosts(monkeypatch):
    # Let monkeypatch restore the guard's process-global replacements too.
    monkeypatch.setattr(socket.socket, "connect", socket.socket.connect)
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", socket.create_connection)
    evaluation.block_external_sockets()
    with pytest.raises(RuntimeError, match="External network disabled"):
        socket.getaddrinfo("api.groq.com", 443)
    with pytest.raises(RuntimeError, match="External network disabled"):
        socket.create_connection(("8.8.8.8", 443))
