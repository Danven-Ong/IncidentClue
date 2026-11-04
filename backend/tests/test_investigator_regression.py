"""Offline approval, provenance and failure boundaries for investigator regression."""

import importlib.util
import json
from pathlib import Path

import httpx2
from openai import BadRequestError
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("investigator_regression", ROOT / "evals/rag/investigator_regression.py")
regression = importlib.util.module_from_spec(spec)
spec.loader.exec_module(regression)


@pytest.fixture(autouse=True)
def current_offline_protocol(tmp_path, monkeypatch):
    # Preserve the approved v1 protocol; a temporary test-only plan pins current code.
    value = json.loads(regression.PROTOCOL.read_text())
    value["runtime"] = regression.runtime_fingerprint()
    value["dependencies"] = {name: regression.digest(regression.HERE / name) for name in regression.DEPENDENCIES}
    path = tmp_path / "offline-test-protocol.json"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(regression, "PROTOCOL", path)


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_fake_graph_is_no_rag_bounded_and_does_not_construct_live_client(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Fake mode accessed credentials, database, model files or provider")

    monkeypatch.setattr(regression, "create_groq_client", forbidden)
    monkeypatch.setattr(regression.execution, "LocalEmbedder", forbidden)
    monkeypatch.setattr(regression.execution, "prepare_schema", forbidden)
    path = tmp_path / "fake.jsonl"
    summary = regression.run(path, env_file=tmp_path / "must-not-read.env")
    assert summary["provider_calls"] == 0 and summary["quality_pass"] is None
    assert summary["all_investigations_succeeded"] is True
    assert summary["model_attempts"] == 15 and summary["tool_attempts"] == 5
    assert summary["completion_tokens_reserved"] == 10240
    records = [r for r in rows(path) if r["type"] == "investigation"]
    assert [r["case_id"] for r in records] == list(regression.CASE_IDS)
    for record in records:
        assert record["arm"] == "no_rag" and record["retrieved_sources"] == []
        assert not record["retrieval_attempted"] and not record["retrieval_completed"]
        assert record["model_attempts"] == 3 and record["tool_attempts"] == 1
        assert regression.execution.suite.recorded_generation_contract_valid(record) is True
        assert record["diagnosis"]["status"] == "insufficient_evidence"  # scripted, not semantic success
        assert all("get_runbook_context" not in [t["function"]["name"] for t in request.get("tools", [])]
                   for request in record["requests"])
        assert all("positive_controls" not in json.dumps(request) for request in record["requests"])
    with pytest.raises(FileExistsError):
        regression.run(path)


@pytest.mark.parametrize("approval", [None, "old-or-incorrect-protocol"])
def test_unapproved_live_refuses_before_credentials_or_output(tmp_path, monkeypatch, approval):
    monkeypatch.setattr(regression, "plan", lambda: pytest.fail("Approval must be checked first"))
    path = tmp_path / "unauthorized.jsonl"
    with pytest.raises(ValueError, match="approval"):
        regression.run(path, live=True, approval=approval, env_file=tmp_path / "private.env")
    assert not path.exists()


def test_source_or_runtime_drift_refuses_before_live_client(tmp_path, monkeypatch):
    monkeypatch.setattr(regression, "runtime_fingerprint", lambda: {"changed": True})
    monkeypatch.setattr(regression, "create_groq_client", lambda *a: pytest.fail("Constructed client"))
    path = tmp_path / "changed.jsonl"
    with pytest.raises(ValueError, match="mismatch"):
        regression.run(path, live=True, approval=regression.digest(regression.PROTOCOL))
    assert not path.exists()


@pytest.mark.parametrize("kind", ["structured", "validation", "unknown"])
def test_first_answer_or_unknown_failure_stops_and_preserves_charge(tmp_path, monkeypatch, kind):
    class Failure(regression.execution.SmokeClient):
        def create(self, **request):
            if "response_format" in request:
                if kind == "structured":
                    response = httpx2.Response(400, request=httpx2.Request("POST", "https://invalid.example"))
                    raise BadRequestError("private-provider-detail", response=response,
                                          body={"error": {"code": "json_validate_failed"}})
                if kind == "validation":
                    raise regression.execution.DiagnosisError("private-provider-detail")
                raise RuntimeError("private-provider-detail")
            return super().create(**request)

    monkeypatch.setattr(regression.execution, "SmokeClient", Failure)
    path = tmp_path / "failed.jsonl"
    summary = regression.run(path)
    assert summary["attempted_investigations"] == summary["failed_investigations"] == 1
    assert not summary["all_investigations_succeeded"]
    assert summary["model_attempts"] == 3 and summary["tool_attempts"] == 1
    assert summary["completion_tokens_reserved"] == 2048
    recorded = rows(path)
    stopped = next(r for r in recorded if r["type"] == "stopped")
    assert stopped["remaining"] == list(regression.CASE_IDS[1:])
    investigation = next(r for r in recorded if r["type"] == "investigation")
    assert investigation["attempts"][-1]["status"] == "exception"
    assert investigation["attempts"][-1]["usage"] is None
    assert "private-provider-detail" not in path.read_text()


def test_output_inside_application_repository_is_refused():
    with pytest.raises(ValueError, match="outside application Git"):
        regression.run(ROOT / "do-not-create.jsonl")


def test_cli_live_without_approval_cannot_start(tmp_path):
    path = tmp_path / "unauthorized.jsonl"
    assert regression.main(["--live", "--output", str(path)]) == 1
    assert not path.exists()


def test_original_approved_protocol_refuses_changed_generation_contract(monkeypatch):
    monkeypatch.setattr(regression, "PROTOCOL", regression.HERE / "investigator-regression-protocol.v1.json")
    with pytest.raises(ValueError, match="mismatch"):
        regression.plan()
