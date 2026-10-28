"""Bounded real-SDK tool requests, service scope, and evidence provenance."""

from copy import deepcopy
import json
from pathlib import Path

import httpx2
from openai import APIStatusError, APITimeoutError, OpenAI
import pytest

from incidentclue import investigation
from incidentclue.diagnosis import DiagnosisError, InputLimitError
from incidentclue.llm import ConfigurationError, DEFAULT_MODEL, GROQ_BASE_URL
from incidentclue.schemas import Incident
from incidentclue.tools import (
    MAX_HEALTH_FILE_BYTES, HealthSnapshot, get_service_health, load_health_snapshot,
    load_log_snapshot,
)


@pytest.fixture
def health():
    path = Path(__file__).resolve().parents[2] / "data/synthetic/checkout-health.json"
    return load_health_snapshot(path)


@pytest.fixture
def incident(incident_data):
    incident_data["evidence"] = []
    return Incident.model_validate(incident_data)


def completion(message, reason="stop"):
    return {
        "id": "test-response", "object": "chat.completion", "created": 1,
        "model": DEFAULT_MODEL,
        "choices": [{"index": 0, "message": {"role": "assistant", **message},
                     "finish_reason": reason}],
    }


@pytest.fixture
def selection():
    return completion({"content": None, "tool_calls": [{
        "id": "call-health", "type": "function", "function": {
            "name": "get_service_health", "arguments": '{"service":"checkout"}',
        },
    }]}, "tool_calls")


@pytest.fixture
def final():
    return completion({"content": json.dumps({
        "status": "hypothesis", "probable_cause": "Missing PAYMENT_API_URL.",
        "evidence_ids": ["H1"], "missing_evidence": ["Deployment configuration."],
        "next_checks": ["Inspect recorded runtime configuration."],
    })})


def fake_client(responses, requests):
    def handler(request):
        requests.append(json.loads(request.content))
        item = responses[len(requests) - 1]
        if item == "timeout":
            raise httpx2.ReadTimeout("Private timeout detail", request=request)
        if isinstance(item, int):
            return httpx2.Response(item, json={"error": {"message": "Private provider detail"}})
        return httpx2.Response(200, json=item)
    return OpenAI(api_key="test-key", base_url=GROQ_BASE_URL,
                  http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def abstention():
    return completion({"content": json.dumps({
        "status": "insufficient_evidence", "probable_cause": None,
        "evidence_ids": [], "missing_evidence": ["Health observations."],
        "next_checks": ["Collect health observations."],
    })})


def test_tool_result_reaches_final_request_and_citations_use_fetched_evidence(
    incident, health, selection, final,
):
    before = incident.model_dump()
    requests, events = [], []
    with fake_client([selection, final], requests) as client:
        result = investigation.investigate_incident(incident, client, health, events=events)
    assert result.evidence_ids == ["H1"]
    assert incident.model_dump() == before
    first, second = requests
    assert first["tool_choice"] == "auto"
    assert first["parallel_tool_calls"] is False
    assert first["tools"][0]["function"]["name"] == "get_service_health"
    assert "response_format" not in first
    assert "PAYMENT_API_URL" not in json.dumps(first)
    assert "tools" not in second
    assert second["response_format"]["json_schema"]["strict"] is True
    reply = next(item for item in second["messages"] if item["role"] == "tool")
    assert reply["tool_call_id"] == "call-health"
    assert json.loads(reply["content"])["evidence"][0]["id"] == "H1"
    assert events[0]["result"]["status"] == "ok"


def test_model_can_skip_lookup_and_still_get_validated_final(incident, health):
    requests, events = [], []
    with fake_client([completion({"content": "No lookup needed."}), abstention()], requests) as client:
        result = investigation.investigate_incident(incident, client, health, events=events)
    assert result.status == "insufficient_evidence"
    assert events == [{"outcome": "not_requested"}]
    assert all(item["role"] != "tool" for item in requests[1]["messages"])


@pytest.mark.parametrize("arguments", [
    "not json", "[]", "null", "{}", '{"service":1}',
    '{"service":"checkout","command":"restart"}',
    '{"service":"' + "x" * 600 + '"}',
])
def test_invalid_arguments_never_execute(incident, health, selection, arguments, monkeypatch):
    selection["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = arguments
    monkeypatch.setattr(investigation, "get_service_health", lambda *a, **k: pytest.fail("executed"))
    requests, events = [], []
    with fake_client([selection], requests) as client, pytest.raises(DiagnosisError, match="invalid tool arguments"):
        investigation.investigate_incident(incident, client, health, events=events)
    assert len(requests) == 1
    assert events == [{"outcome": "rejected", "reason": "invalid_arguments"}]


def test_unknown_tool_never_dispatches(incident, health, selection, monkeypatch):
    selection["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "restart_service"
    monkeypatch.setattr(investigation, "get_service_health", lambda *a, **k: pytest.fail("executed"))
    with fake_client([selection], []) as client, pytest.raises(DiagnosisError, match="unsupported tool"):
        investigation.investigate_incident(incident, client, health)


def test_multiple_calls_rejected_before_any_execution(incident, health, selection, monkeypatch):
    calls = selection["choices"][0]["message"]["tool_calls"]
    calls.append(deepcopy(calls[0]))
    monkeypatch.setattr(investigation, "get_service_health", lambda *a, **k: pytest.fail("executed"))
    with fake_client([selection], []) as client, pytest.raises(DiagnosisError, match="budget exceeded"):
        investigation.investigate_incident(incident, client, health)


@pytest.mark.parametrize("failure", ["wrong_service", "unavailable", "io_error"])
def test_tool_failure_is_not_health_evidence(incident, health, selection, failure, monkeypatch):
    if failure == "wrong_service":
        selection["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"service":"payments"}'
    elif failure == "unavailable":
        health.evidence = []
    else:
        def fail(*args, **kwargs):
            raise OSError("Private backend detail")
        monkeypatch.setattr(investigation, "get_service_health", fail)
    requests, events = [], []
    with fake_client([selection, abstention()], requests) as client:
        result = investigation.investigate_incident(incident, client, health, events=events)
    assert result.probable_cause is None
    assert events[0]["result"]["status"] == "error"
    assert events[0]["result"]["evidence"] == []
    assert "Private backend detail" not in json.dumps(requests)


def test_cannot_cite_unreturned_tool_observations(incident, health, selection, final):
    health.evidence = []
    with fake_client([selection, final], []) as client, pytest.raises(DiagnosisError, match="outside"):
        investigation.investigate_incident(incident, client, health)


def test_tool_evidence_id_collision_rejected(incident, health, selection):
    incident.evidence = health.evidence.copy()
    with fake_client([selection], []) as client, pytest.raises(DiagnosisError, match="collide"):
        investigation.investigate_incident(incident, client, health)


@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_incomplete_selection_fails_without_final_request(incident, health, selection, reason):
    selection["choices"][0]["finish_reason"] = reason
    requests = []
    with fake_client([selection], requests) as client, pytest.raises(DiagnosisError):
        investigation.investigate_incident(incident, client, health)
    assert len(requests) == 1


@pytest.mark.parametrize("responses", [[429], ["timeout"], ["selection", "timeout"]])
def test_provider_failures_propagate_without_retries(incident, health, selection, responses):
    responses = [selection if item == "selection" else item for item in responses]
    requests = []
    with fake_client(responses, requests) as client, pytest.raises((APIStatusError, APITimeoutError)):
        investigation.investigate_incident(incident, client, health)
    assert len(requests) == len(responses)


def test_oversized_input_and_blank_model_fail_before_network(incident, health):
    requests = []
    with fake_client([], requests) as client:
        with pytest.raises(ConfigurationError):
            investigation.investigate_incident(incident, client, health, " ")
        incident.summary = "x" * 12_001
        with pytest.raises(InputLimitError):
            investigation.investigate_incident(incident, client, health)
    assert requests == []


def test_enriched_input_budget_checked_before_final_request(incident, health, selection):
    incident.summary = "x" * 11_900
    requests = []
    with fake_client([selection], requests) as client, pytest.raises(InputLimitError):
        investigation.investigate_incident(incident, client, health)
    assert len(requests) == 1


def test_scope_does_not_return_other_services_snapshot(health):
    result = get_service_health("payments", allowed_service="payments", snapshot=health)
    assert result == {"status": "error", "error": "health_unavailable", "evidence": []}


@pytest.mark.parametrize("payload", [b"\xff", b"{}", b" " * (MAX_HEALTH_FILE_BYTES + 1)])
def test_health_loader_bounds_and_validates_file(tmp_path, payload):
    path = tmp_path / "health.json"
    path.write_bytes(payload)
    with pytest.raises(ConfigurationError):
        load_health_snapshot(path)


def test_health_snapshot_rejects_logs(health):
    payload = health.model_dump()
    payload["evidence"][0]["kind"] = "log"
    with pytest.raises(ValueError):
        HealthSnapshot.model_validate(payload)


def test_final_cannot_request_another_tool(incident, health, selection):
    requests = []
    with fake_client([selection, selection], requests) as client, pytest.raises(DiagnosisError):
        investigation.investigate_incident(incident, client, health)
    assert len(requests) == 2


def test_investigate_cli_records_tool_evidence_and_prints_diagnosis(
    incident, health, selection, final, tmp_path, monkeypatch, capsys,
):
    from incidentclue import cli
    incident_path = tmp_path / "incident.json"
    health_path = tmp_path / "health.json"
    incident_path.write_text(incident.model_dump_json())
    health_path.write_text(health.model_dump_json())
    requests = []
    monkeypatch.setattr(cli, "create_groq_client", lambda key: fake_client([selection, final], requests))
    record_dir = tmp_path / "runs"
    assert cli.main([
        "investigate", str(incident_path), "--health-file", str(health_path),
        "--record-dir", str(record_dir),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["evidence_ids"] == ["H1"]
    record = json.loads(next(record_dir.glob("*.json")).read_text())
    assert record["tool_events"][0]["result"]["evidence"][0]["id"] == "H1"
    assert record["request_policy"]["max_model_requests"] == 2
    assert record["request_policy"]["max_tool_calls"] == 1
    assert record["health_snapshot"]["serialized_sha256"]
    assert record["selection_instructions_sha256"]


def test_cli_bad_health_configuration_fails_before_client(tmp_path, incident, monkeypatch, capsys):
    from incidentclue import cli
    path = tmp_path / "incident.json"
    path.write_text(incident.model_dump_json())
    monkeypatch.setattr(cli, "create_groq_client", lambda key: pytest.fail("client created"))
    assert cli.main([
        "investigate", str(path), "--health-file", str(tmp_path / "missing.json"),
    ]) == 2
    assert "synthetic health snapshot" in capsys.readouterr().err


def test_cli_rejected_tool_keeps_safe_trace(tmp_path, incident, health, selection, monkeypatch, capsys):
    from incidentclue import cli
    selection["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "private-unknown-tool"
    path, snapshot = tmp_path / "incident.json", tmp_path / "health.json"
    path.write_text(incident.model_dump_json())
    snapshot.write_text(health.model_dump_json())
    monkeypatch.setattr(cli, "create_groq_client", lambda key: fake_client([selection], []))
    assert cli.main([
        "investigate", str(path), "--health-file", str(snapshot),
        "--record-dir", str(tmp_path / "runs"),
    ]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "private-unknown-tool" not in output.err
    record = json.loads(next((tmp_path / "runs").glob("*.json")).read_text())
    assert record["tool_events"] == [{"outcome": "rejected", "reason": "unknown_tool"}]


@pytest.fixture
def logs():
    return load_log_snapshot(Path(__file__).resolve().parents[2] / "data/synthetic/checkout-logs.json")


@pytest.fixture
def log_selection(selection):
    function = selection["choices"][0]["message"]["tool_calls"][0]["function"]
    function["name"] = "get_logs"
    function["arguments"] = json.dumps({
        "service": "checkout", "start_time": "2026-10-28T02:00:00Z",
        "end_time": "2026-10-28T02:05:00Z",
    })
    return selection


@pytest.fixture
def log_final(final):
    payload = json.loads(final["choices"][0]["message"]["content"])
    payload["evidence_ids"] = ["L2"]
    final["choices"][0]["message"]["content"] = json.dumps(payload)
    return final


def test_logs_selected_from_two_tools_and_returned_as_citable_evidence(
    incident, health, logs, log_selection, log_final,
):
    requests, events = [], []
    with fake_client([log_selection, log_final], requests) as client:
        result = investigation.investigate_incident(incident, client, health, logs=logs, events=events)
    assert result.evidence_ids == ["L2"]
    assert {tool["function"]["name"] for tool in requests[0]["tools"]} == {"get_logs", "get_service_health"}
    assert "PAYMENT_API_URL" not in json.dumps(requests[0])
    assert events[0]["name"] == "get_logs"
    assert [item["id"] for item in events[0]["result"]["evidence"]] == ["L1", "L2"]
    assert events[0]["arguments"]["start_time"].startswith("2026-10-28")
    assert incident.evidence == []


def test_health_can_still_be_selected_when_logs_offered(incident, health, logs, selection, final):
    with fake_client([selection, final], []) as client:
        assert investigation.investigate_incident(incident, client, health, logs=logs).evidence_ids == ["H1"]


def test_unconfigured_log_tool_rejected(incident, health, log_selection):
    with fake_client([log_selection], []) as client, pytest.raises(DiagnosisError, match="unsupported"):
        investigation.investigate_incident(incident, client, health)


def test_cannot_cite_logs_outside_returned_window(incident, health, logs, log_selection, log_final):
    payload = json.loads(log_final["choices"][0]["message"]["content"])
    payload["evidence_ids"] = ["L0"]
    log_final["choices"][0]["message"]["content"] = json.dumps(payload)
    with fake_client([log_selection, log_final], []) as client, pytest.raises(DiagnosisError, match="outside"):
        investigation.investigate_incident(incident, client, health, logs=logs)


def test_invalid_log_window_stops_before_execution(incident, health, logs, log_selection, monkeypatch):
    log_selection["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"service":"checkout","start_time":"bad","end_time":"bad"}'
    monkeypatch.setattr(investigation, "get_logs", lambda *a, **k: pytest.fail("executed"))
    with fake_client([log_selection], []) as client, pytest.raises(DiagnosisError, match="invalid tool arguments"):
        investigation.investigate_incident(incident, client, health, logs=logs)


def test_log_backend_error_is_sanitized_and_allows_abstention(incident, health, logs, log_selection, monkeypatch):
    def fail(*a, **k):
        raise OSError("Private log backend detail")
    monkeypatch.setattr(investigation, "get_logs", fail)
    events = []
    with fake_client([log_selection, abstention()], []) as client:
        result = investigation.investigate_incident(incident, client, health, logs=logs, events=events)
    assert result.status == "insufficient_evidence"
    assert events[0]["result"] == {"status": "error", "error": "logs_unavailable", "evidence": []}


def test_cli_enables_and_records_log_source(tmp_path, incident, health, logs, log_selection, log_final, monkeypatch, capsys):
    from incidentclue import cli
    incident_path, health_path, logs_path = [tmp_path / name for name in ("incident.json", "health.json", "logs.json")]
    for path, value in ((incident_path, incident), (health_path, health), (logs_path, logs)):
        path.write_text(value.model_dump_json())
    monkeypatch.setattr(cli, "create_groq_client", lambda key: fake_client([log_selection, log_final], []))
    assert cli.main([
        "investigate", str(incident_path), "--health-file", str(health_path),
        "--logs-file", str(logs_path), "--record-dir", str(tmp_path / "runs"),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["evidence_ids"] == ["L2"]
    record = json.loads(next((tmp_path / "runs").glob("*.json")).read_text())
    assert record["log_snapshot"]["serialized_sha256"]
    assert record["tool_events"][0]["name"] == "get_logs"


def test_cli_invalid_log_source_fails_before_provider(tmp_path, incident, health, monkeypatch, capsys):
    from incidentclue import cli
    incident_path, health_path = tmp_path / "incident.json", tmp_path / "health.json"
    incident_path.write_text(incident.model_dump_json())
    health_path.write_text(health.model_dump_json())
    monkeypatch.setattr(cli, "create_groq_client", lambda key: pytest.fail("client created"))
    assert cli.main([
        "investigate", str(incident_path), "--health-file", str(health_path),
        "--logs-file", str(tmp_path / "missing.json"),
    ]) == 2
    assert "synthetic log snapshot" in capsys.readouterr().err
