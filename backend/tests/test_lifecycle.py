"""Real checkpoints, retained graph state and lifecycle failure semantics."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from incidentclue.lifecycle import Lifecycle
from incidentclue.llm import ConfigurationError
from incidentclue.persistence import PersistenceError, RecoveryRequired
from incidentclue.schemas import Incident
from incidentclue.tools import load_health_snapshot
from test_investigation import completion, fake_client

pytestmark = pytest.mark.postgres


@pytest.fixture
def inputs(incident_data):
    incident_data["evidence"] = []
    root = Path(__file__).resolve().parents[2]
    return Incident.model_validate(incident_data), load_health_snapshot(root / "data/synthetic/checkout-health.json")


def responses():
    return [
        completion({"content": None, "tool_calls": [{
            "id": "call-health", "type": "function", "function": {
                "name": "get_service_health", "arguments": '{"service":"checkout"}',
            },
        }]}, "tool_calls"),
        completion({"content": "Enough evidence."}),
        completion({"content": json.dumps({
            "status": "hypothesis", "probable_cause": "Missing PAYMENT_API_URL.",
            "evidence_ids": ["H1"], "missing_evidence": ["Deployment configuration."],
            "next_checks": ["Inspect recorded runtime configuration."],
        })}),
    ]


def test_pause_resume_preserves_identity_evidence_history_and_budget(db, inputs):
    service = Lifecycle(db)
    identifier = service.start(*inputs, paused=True)
    before = service.inspect(identifier)
    assert before["status"] == "paused"
    assert before["charged"] == {"model": 0, "tool": 0}
    requests = []
    paused = service.resume(identifier, lambda: fake_client(responses(), requests), pause_after="execute")
    assert paused["status"] == "paused" and paused["next_nodes"] == ["decide"]
    assert paused["charged"] == {"model": 1, "tool": 1}
    assert paused["state"]["tool_calls_used"] == 1
    assert paused["state"]["model_requests_used"] == 1
    assert len(paused["state"]["tool_history"]) == 1
    later_requests = []
    completed = service.resume(identifier, lambda: fake_client(responses()[1:], later_requests))
    assert completed["investigation_id"] == before["investigation_id"]
    assert completed["status"] == "completed" and not completed["executing"]
    assert completed["state"]["evidence"] == paused["state"]["evidence"]
    assert completed["state"]["tool_history"] == paused["state"]["tool_history"]
    assert completed["state"]["diagnosis"]["evidence_ids"] == ["H1"]
    assert completed["charged"] == {"model": 3, "tool": 1}
    assert len(requests) + len(later_requests) == 3
    again = service.resume(identifier, lambda: pytest.fail("completed run executed"))
    assert again["state"] == completed["state"]


@pytest.mark.parametrize("boundary", ["decide", "execute", "diagnose", "validate"])
def test_each_deliberate_boundary(db, inputs, boundary):
    service = Lifecycle(db)
    identifier = service.start(*inputs)
    requests = []
    first = service.resume(identifier, lambda: fake_client(responses(), requests), pause_after=boundary)
    assert first["status"] == ("completed" if boundary == "validate" else "paused")
    rest = []
    final = service.resume(identifier, lambda: fake_client(responses()[len(requests):], rest))
    assert final["status"] == "completed"
    assert len(requests) + len(rest) == 3


def test_failed_provider_is_charged_and_terminal(db, inputs):
    service = Lifecycle(db)
    identifier = service.start(*inputs)
    requests = []
    with pytest.raises(PersistenceError, match="Provider request failed"):
        service.resume(identifier, lambda: fake_client([429], requests))
    failed = service.inspect(identifier)
    assert failed["status"] == "failed" and failed["charged"]["model"] == 1
    with pytest.raises(PersistenceError, match="cannot resume"):
        service.resume(identifier, lambda: pytest.fail("failed run executed"))
    assert service.pause(identifier)["status"] == "failed"


def test_missing_current_credentials_keeps_run_resumable(db, inputs):
    service = Lifecycle(db)
    identifier = service.start(*inputs)
    def missing():
        raise ConfigurationError("Set GROQ_API_KEY.")
    with pytest.raises(ConfigurationError):
        service.resume(identifier, missing)
    view = service.inspect(identifier)
    assert view["status"] == "paused" and view["charged"]["model"] == 0
    assert service.resume(identifier, lambda: fake_client(responses(), []))["status"] == "completed"


def test_incompatible_runtime_is_inspectable_and_refused(db, inputs, monkeypatch):
    from incidentclue import lifecycle
    service = Lifecycle(db)
    identifier = service.start(*inputs, paused=True)
    monkeypatch.setattr(lifecycle, "runtime_fingerprint", lambda: {"graph_contract": 999})
    assert not service.inspect(identifier)["compatible"]
    with pytest.raises(PersistenceError, match="incompatible"):
        service.resume(identifier, lambda: pytest.fail("incompatible run executed"))
    assert service.inspect(identifier)["status"] == "paused"


def test_missing_id_and_invalid_boundary(db):
    service = Lifecycle(db)
    with pytest.raises(PersistenceError, match="not found"):
        service.resume(str(uuid4()), None)
    with pytest.raises(PersistenceError, match="graph node"):
        service.resume(str(uuid4()), None, pause_after="anything")


def test_uncertain_attempt_recovery_refuses_and_keeps_budget(db, inputs):
    service = Lifecycle(db)
    identifier = service.start(*inputs)
    class ProcessDeath(BaseException):
        pass
    # A death inside a request bypasses ordinary exception cleanup.
    import httpx2
    from openai import OpenAI
    def crashing_client():
        return OpenAI(api_key="fake", http_client=httpx2.Client(transport=httpx2.MockTransport(
            lambda _: (_ for _ in ()).throw(ProcessDeath()),
        )))
    with pytest.raises(ProcessDeath):
        service.resume(identifier, crashing_client)
    assert service.inspect(identifier)["recovery_needed"]
    with pytest.raises(RecoveryRequired, match="uncertain"):
        service.resume(identifier, lambda: pytest.fail("replayed ambiguous call"))
    view = service.inspect(identifier)
    assert view["status"] == "recovery_required" and view["charged"]["model"] == 1


def test_invalid_saved_state_refused_before_a_request(db, inputs):
    from incidentclue.lifecycle import graph_config
    service = Lifecycle(db)
    identifier = service.start(*inputs, paused=True)
    graph = service._graph(db.get(identifier))
    graph.update_state(graph_config(identifier), {"model_requests_used": -1})
    with pytest.raises(PersistenceError, match="validation failed"):
        service.resume(identifier, lambda: pytest.fail("invalid state executed"))
    assert service.inspect(identifier)["status"] == "failed"


def test_pause_racing_between_read_and_begin_is_not_lost(db, inputs, monkeypatch):
    service = Lifecycle(db)
    identifier = service.start(*inputs, paused=True)
    original = db.begin
    def racing_begin(identifier, expected_updated_at):
        db.request_pause(identifier)
        original(identifier, expected_updated_at)
    monkeypatch.setattr(db, "begin", racing_begin)
    view = service.resume(identifier, lambda: fake_client([], []))
    assert view["status"] == "paused" and view["charged"] == {"model": 0, "tool": 0}


@pytest.mark.parametrize("deny_second_tool", [False, True])
def test_two_tool_budget_is_retained_across_multiple_resumes(db, inputs, deny_second_tool):
    from incidentclue.tools import load_log_snapshot
    logs = load_log_snapshot(Path(__file__).resolve().parents[2] / "data/synthetic/checkout-logs.json")
    service = Lifecycle(db)
    identifier = service.start(*inputs, logs=logs)
    first = service.resume(identifier, lambda: fake_client(responses()[:1], []), pause_after="execute")
    assert first["charged"] == {"model": 1, "tool": 1}
    selection = completion({"content": None, "tool_calls": [{
        "id": "call-logs", "type": "function", "function": {
            "name": "get_logs", "arguments": json.dumps({
                "service": "payments" if deny_second_tool else "checkout",
                "start_time": "2026-10-28T02:00:00Z", "end_time": "2026-10-28T02:05:00Z",
            }),
        },
    }]}, "tool_calls")
    second = service.resume(identifier, lambda: fake_client([selection], []), pause_after="execute")
    assert second["next_nodes"] == ["diagnose"]
    assert second["charged"] == {"model": 2, "tool": 2}
    final_requests = []
    done = service.resume(identifier, lambda: fake_client(responses()[2:], final_requests))
    assert done["charged"] == {"model": 3, "tool": 2}
    assert done["state"]["tool_calls_used"] == 2 and len(done["state"]["tool_history"]) == 2
    assert len(final_requests) == 1 and "tools" not in final_requests[0]
    if deny_second_tool:
        assert done["state"]["tool_history"][1]["result"]["error"] == "service_not_allowed"


def test_duplicate_lookup_after_resume_is_not_executed_again(db, inputs):
    service = Lifecycle(db)
    identifier = service.start(*inputs)
    service.resume(identifier, lambda: fake_client(responses()[:1], []), pause_after="execute")
    duplicate = responses()[0]
    duplicate["choices"][0]["message"]["tool_calls"][0]["id"] = "call-repeat"
    done = service.resume(identifier, lambda: fake_client([duplicate, responses()[2]], []))
    assert done["charged"] == {"model": 3, "tool": 1}
    assert done["state"]["stop_reason"] == "repeated_request"
    assert len(done["state"]["tool_history"]) == 1
