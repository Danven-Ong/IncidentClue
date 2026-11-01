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


@pytest.mark.parametrize("malformation", [
    "null_choices", "missing_choices", "null_message", "missing_finish",
    "null_function", "null_arguments", "null_identifier", "invalid_calls",
])
def test_malformed_selection_is_controlled_without_execution(incident, health, selection, malformation, monkeypatch):
    if malformation == "null_choices":
        selection["choices"] = None
    elif malformation == "missing_choices":
        selection.pop("choices")
    elif malformation == "null_message":
        selection["choices"][0]["message"] = None
    elif malformation == "missing_finish":
        selection["choices"][0].pop("finish_reason")
    elif malformation == "invalid_calls":
        selection["choices"][0]["message"]["tool_calls"] = "invalid"
    else:
        call = selection["choices"][0]["message"]["tool_calls"][0]
        if malformation == "null_function":
            call["function"] = None
        elif malformation == "null_arguments":
            call["function"]["arguments"] = None
        else:
            call["id"] = None
    monkeypatch.setattr(investigation, "get_service_health", lambda *a, **k: pytest.fail("executed"))
    with fake_client([selection], []) as client, pytest.raises(DiagnosisError):
        investigation.investigate_incident(incident, client, health)


def test_malformed_selection_cli_still_writes_failure_record(tmp_path, incident, health, selection, monkeypatch, capsys):
    from incidentclue import cli
    selection["choices"] = None
    source, snapshot = tmp_path / "incident.json", tmp_path / "health.json"
    source.write_text(incident.model_dump_json())
    snapshot.write_text(health.model_dump_json())
    monkeypatch.setattr(cli, "create_groq_client", lambda key: fake_client([selection], []))
    assert cli.main([
        "investigate", str(source), "--health-file", str(snapshot),
        "--record-dir", str(tmp_path / "runs"),
    ]) == 1
    output = capsys.readouterr()
    assert output.out == "" and "Traceback" not in output.err
    record = json.loads(next((tmp_path / "runs").glob("*.json")).read_text())
    assert record["outcome"] == "diagnosis_error" and record["exit_code"] == 1


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
    with fake_client([selection, completion({"content": "Enough evidence."}), final], requests) as client:
        result = investigation.investigate_incident(incident, client, health, events=events)
    assert result.evidence_ids == ["H1"]
    assert incident.model_dump() == before
    first, _, second = requests
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


@pytest.mark.parametrize("model", [DEFAULT_MODEL, "openai/gpt-oss-120b", "custom-test-model"])
def test_selection_policy_scopes_tools_and_keeps_generation_caps(incident, health, logs, model):
    requests = []
    original_health_tool = deepcopy(investigation.HEALTH_TOOL)
    original_log_tool = deepcopy(investigation.LOG_TOOL)
    with fake_client([completion({"content": "done"}), abstention()], requests) as client:
        investigation.investigate_incident(incident, client, health, model, logs=logs)
    selection, diagnosis = requests
    if model.startswith("openai/gpt-oss-"):
        assert selection["reasoning_effort"] == "low"
    else:
        assert "reasoning_effort" not in selection
    assert "reasoning_effort" not in diagnosis
    assert selection["max_completion_tokens"] == 512
    assert diagnosis["max_completion_tokens"] == 1024
    assert {tool["function"]["name"] for tool in selection["tools"]} == {"get_service_health", "get_logs"}
    for tool in selection["tools"]:
        assert tool["function"]["parameters"]["properties"]["service"]["enum"] == [incident.service]
    assert investigation.HEALTH_TOOL == original_health_tool
    assert investigation.LOG_TOOL == original_log_tool


def test_health_only_prompt_and_schema_do_not_offer_logs(incident, health):
    requests = []
    with fake_client([completion({"content": "done"}), abstention()], requests) as client:
        investigation.investigate_incident(incident, client, health)
    selection = requests[0]
    assert [tool["function"]["name"] for tool in selection["tools"]] == ["get_service_health"]
    assert "get_logs" not in json.dumps(selection)


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
    with fake_client([selection, completion({"content": "Enough evidence."}), abstention()], requests) as client:
        result = investigation.investigate_incident(incident, client, health, events=events)
    assert result.probable_cause is None
    assert events[0]["result"]["status"] == "error"
    assert events[0]["result"]["evidence"] == []
    assert "Private backend detail" not in json.dumps(requests)


def test_cannot_cite_unreturned_tool_observations(incident, health, selection, final):
    health.evidence = []
    with fake_client([selection, completion({"content": "Enough evidence."}), final], []) as client, pytest.raises(DiagnosisError, match="outside"):
        investigation.investigate_incident(incident, client, health)


def test_tool_evidence_id_collision_rejected(incident, health, selection):
    incident.evidence = [health.evidence[0].model_copy(update={"content": "Conflicting observation."})]
    with fake_client([selection], []) as client, pytest.raises(DiagnosisError, match="Conflicting"):
        investigation.investigate_incident(incident, client, health)


@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_incomplete_selection_fails_without_final_request(incident, health, selection, reason):
    selection["choices"][0]["finish_reason"] = reason
    requests = []
    with fake_client([selection], requests) as client, pytest.raises(DiagnosisError):
        investigation.investigate_incident(incident, client, health)
    assert len(requests) == 1
    assert requests[0]["reasoning_effort"] == "low"
    assert requests[0]["max_completion_tokens"] == 512


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
    with fake_client([selection, completion({"content": "Enough evidence."}), selection], requests) as client, pytest.raises(DiagnosisError):
        investigation.investigate_incident(incident, client, health)
    assert len(requests) == 3


def test_investigate_cli_records_tool_evidence_and_prints_diagnosis(
    incident, health, selection, final, tmp_path, monkeypatch, capsys,
):
    from incidentclue import cli
    incident_path = tmp_path / "incident.json"
    health_path = tmp_path / "health.json"
    incident_path.write_text(incident.model_dump_json())
    health_path.write_text(health.model_dump_json())
    requests = []
    monkeypatch.setattr(cli, "create_groq_client", lambda key: fake_client([selection, completion({"content": "Enough evidence."}), final], requests))
    record_dir = tmp_path / "runs"
    assert cli.main([
        "investigate", str(incident_path), "--health-file", str(health_path),
        "--record-dir", str(record_dir),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["evidence_ids"] == ["H1"]
    record = json.loads(next(record_dir.glob("*.json")).read_text())
    assert record["tool_events"][0]["result"]["evidence"][0]["id"] == "H1"
    assert record["request_policy"]["max_model_requests"] == 3
    assert record["request_policy"]["max_tool_calls"] == 2
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
    selection = deepcopy(selection)
    function = selection["choices"][0]["message"]["tool_calls"][0]["function"]
    function["name"] = "get_logs"
    function["arguments"] = json.dumps({
        "service": "checkout", "start_time": "2026-10-28T02:00:00Z",
        "end_time": "2026-10-28T02:05:00Z",
    })
    return selection


@pytest.fixture
def log_final(final):
    final = deepcopy(final)
    payload = json.loads(final["choices"][0]["message"]["content"])
    payload["evidence_ids"] = ["L2"]
    final["choices"][0]["message"]["content"] = json.dumps(payload)
    return final


def test_logs_selected_from_two_tools_and_returned_as_citable_evidence(
    incident, health, logs, log_selection, log_final,
):
    requests, events = [], []
    with fake_client([log_selection, completion({"content": "Enough evidence."}), log_final], requests) as client:
        result = investigation.investigate_incident(incident, client, health, logs=logs, events=events)
    assert result.evidence_ids == ["L2"]
    assert {tool["function"]["name"] for tool in requests[0]["tools"]} == {"get_logs", "get_service_health"}
    assert "PAYMENT_API_URL" not in json.dumps(requests[0])
    assert events[0]["name"] == "get_logs"
    assert [item["id"] for item in events[0]["result"]["evidence"]] == ["L1", "L2"]
    assert events[0]["arguments"]["start_time"].startswith("2026-10-28")
    assert incident.evidence == []


def test_health_can_still_be_selected_when_logs_offered(incident, health, logs, selection, final):
    with fake_client([selection, completion({"content": "Enough evidence."}), final], []) as client:
        assert investigation.investigate_incident(incident, client, health, logs=logs).evidence_ids == ["H1"]


def test_unconfigured_log_tool_rejected(incident, health, log_selection):
    with fake_client([log_selection], []) as client, pytest.raises(DiagnosisError, match="unsupported"):
        investigation.investigate_incident(incident, client, health)


def test_cannot_cite_logs_outside_returned_window(incident, health, logs, log_selection, log_final):
    payload = json.loads(log_final["choices"][0]["message"]["content"])
    payload["evidence_ids"] = ["L0"]
    log_final["choices"][0]["message"]["content"] = json.dumps(payload)
    with fake_client([log_selection, completion({"content": "Enough evidence."}), log_final], []) as client, pytest.raises(DiagnosisError, match="outside"):
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
    with fake_client([log_selection, completion({"content": "Enough evidence."}), abstention()], []) as client:
        result = investigation.investigate_incident(incident, client, health, logs=logs, events=events)
    assert result.status == "insufficient_evidence"
    assert events[0]["result"] == {"status": "error", "error": "logs_unavailable", "evidence": []}


def test_cli_enables_and_records_log_source(tmp_path, incident, health, logs, log_selection, log_final, monkeypatch, capsys):
    from incidentclue import cli
    incident_path, health_path, logs_path = [tmp_path / name for name in ("incident.json", "health.json", "logs.json")]
    for path, value in ((incident_path, incident), (health_path, health), (logs_path, logs)):
        path.write_text(value.model_dump_json())
    monkeypatch.setattr(cli, "create_groq_client", lambda key: fake_client([log_selection, completion({"content": "Enough evidence."}), log_final], []))
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


def test_graph_runs_two_lookups_then_routes_directly_to_final(
    incident, health, logs, selection, log_selection, log_final,
):
    log_selection["choices"][0]["message"]["tool_calls"][0]["id"] = "call-logs"
    requests, events, steps, summary = [], [], [], {}
    with fake_client([selection, log_selection, log_final], requests) as client:
        result = investigation.investigate_incident(
            incident, client, health, logs=logs, events=events, steps=steps, summary=summary,
        )
    assert result.evidence_ids == ["L2"]
    assert steps == ["decide", "execute", "decide", "execute", "diagnose", "validate"]
    assert summary == {"tool_calls_used": 2, "model_requests_used": 3,
                       "stop_reason": "tool_budget_exhausted"}
    assert [item["name"] for item in events] == ["get_service_health", "get_logs"]
    assert "tools" not in requests[-1]
    assert "H1" in json.dumps(requests[1])
    assert "L2" in json.dumps(requests[-1])


def test_repeat_lookup_stops_without_second_execution(incident, health, selection, final, monkeypatch):
    duplicate = deepcopy(selection)
    duplicate["choices"][0]["message"]["tool_calls"][0]["id"] = "call-repeat"
    executed = []
    original = investigation.get_service_health
    def track(*args, **kwargs):
        executed.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(investigation, "get_service_health", track)
    requests, events, summary = [], [], {}
    with fake_client([selection, duplicate, final], requests) as client:
        result = investigation.investigate_incident(incident, client, health, events=events, summary=summary)
    assert result.evidence_ids == ["H1"]
    assert len(executed) == 1
    assert summary["stop_reason"] == "repeated_request"
    assert summary["tool_calls_used"] == 1
    assert events[-1]["reason"] == "repeated_request"
    last_reply = [item for item in requests[-1]["messages"] if item["role"] == "tool"][-1]
    assert json.loads(last_reply["content"])["error"] == "repeated_request"


def test_equivalent_timezone_queries_are_duplicates(incident, health, logs, log_selection, log_final):
    duplicate = deepcopy(log_selection)
    call = duplicate["choices"][0]["message"]["tool_calls"][0]
    call["id"] = "call-equivalent"
    call["function"]["arguments"] = json.dumps({
        "end_time": "2026-10-28T10:05:00+08:00", "service": "checkout",
        "start_time": "2026-10-28T10:00:00+08:00",
    })
    events, summary = [], {}
    with fake_client([log_selection, duplicate, log_final], []) as client:
        investigation.investigate_incident(incident, client, health, logs=logs, events=events, summary=summary)
    assert summary["tool_calls_used"] == 1
    assert summary["stop_reason"] == "repeated_request"


def test_identical_evidence_is_not_appended_again(incident, health, selection, final):
    incident.evidence = [item.model_copy(deep=True) for item in health.evidence]
    with fake_client([selection, completion({"content": "Enough."}), final], []) as client:
        graph = investigation.build_investigation_graph(client, health)
        state = graph.invoke({
            "incident": incident, "evidence": incident.evidence, "tool_history": [],
            "messages": [{"role": "user", "content": incident.model_dump_json()}],
            "tool_calls_used": 0, "model_requests_used": 0, "pending_call": None,
            "stop_reason": "",
        })
    assert [item.id for item in state["evidence"]] == ["H1"]
    assert state["tool_calls_used"] == 1


def test_merge_reducer_preserves_inputs_and_rejects_conflicts(health):
    item = health.evidence[0]
    original = [item]
    assert investigation.merge_evidence(original, [item.model_copy()]) == original
    assert len(original) == 1
    with pytest.raises(DiagnosisError, match="Conflicting"):
        investigation.merge_evidence(original, [item.model_copy(update={"content": "Other fact."})])


def test_tool_history_reducer_caps_growth():
    original = [{"name": "one"}]
    result = investigation.append_tool_history(original, [{"name": "two"}])
    assert len(original) == 1 and len(result) == 2
    with pytest.raises(DiagnosisError, match="history budget"):
        investigation.append_tool_history(result, [{"name": "three"}])


def test_same_call_id_cannot_be_reused(incident, health, selection):
    with fake_client([selection, selection], []) as client, pytest.raises(DiagnosisError, match="reused"):
        investigation.investigate_incident(incident, client, health)


def test_final_invalid_citation_fails_in_validate_node(incident, health, selection, final):
    payload = json.loads(final["choices"][0]["message"]["content"])
    payload["evidence_ids"] = ["invented"]
    final["choices"][0]["message"]["content"] = json.dumps(payload)
    steps = []
    with fake_client([selection, completion({"content": "Enough."}), final], []) as client, pytest.raises(DiagnosisError, match="outside"):
        investigation.investigate_incident(incident, client, health, steps=steps)
    assert steps[-2:] == ["diagnose", "validate"]


def test_second_lookup_failure_retains_first_evidence(incident, health, logs, selection, log_selection, final, monkeypatch):
    log_selection["choices"][0]["message"]["tool_calls"][0]["id"] = "call-logs"
    def fail(*args, **kwargs):
        raise OSError("Private detail")
    monkeypatch.setattr(investigation, "get_logs", fail)
    events = []
    with fake_client([selection, log_selection, final], []) as client:
        result = investigation.investigate_incident(incident, client, health, logs=logs, events=events)
    assert result.evidence_ids == ["H1"]
    assert events[1]["result"]["status"] == "error"


def test_compiled_graph_has_expected_nodes_and_no_checkpointer(health):
    with fake_client([], []) as client:
        graph = investigation.build_investigation_graph(client, health)
    assert set(graph.get_graph().nodes) == {"__start__", "decide", "execute", "diagnose", "validate", "__end__"}
    assert graph.checkpointer is None
