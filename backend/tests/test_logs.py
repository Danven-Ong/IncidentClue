"""Log query validation, scope, time filtering, and bounded synthetic snapshots."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from incidentclue.llm import ConfigurationError
from incidentclue.tools import LogArguments, LogSnapshot, get_logs, load_log_snapshot


@pytest.fixture
def logs():
    path = Path(__file__).resolve().parents[2] / "data/synthetic/checkout-logs.json"
    return load_log_snapshot(path)


def arguments(**overrides):
    return LogArguments.model_validate_json(json.dumps({
        "service": "checkout", "start_time": "2026-10-28T02:00:00Z",
        "end_time": "2026-10-28T02:05:00Z", **overrides,
    }))


def test_filters_by_inclusive_instants_and_preserves_source(logs):
    before = logs.model_dump()
    result = get_logs(arguments(start_time="2026-10-28T10:00:10+08:00",
                                end_time="2026-10-28T10:01:00+08:00"),
                      allowed_service="checkout", snapshot=logs)
    assert [item["id"] for item in result["evidence"]] == ["L1", "L2"]
    assert result["truncated"] is False
    assert logs.model_dump() == before


@pytest.mark.parametrize("overrides", [
    {"start_time": "2026-10-28T02:00:00"},
    {"start_time": "invalid"}, {"start_time": 123},
    {"end_time": "2026-10-28T02:00:00Z"},
    {"end_time": "2026-10-28T01:00:00Z"},
    {"end_time": "2026-10-28T03:00:01Z"},
    {"path": "/etc/passwd"}, {"service": 42},
])
def test_rejects_invalid_or_overbroad_queries(overrides):
    with pytest.raises(ValueError):
        arguments(**overrides)


def test_limits_to_five_latest_and_marks_truncation(logs):
    item = logs.evidence[1].model_dump(mode="json")
    evidence = []
    for number in range(7):
        evidence.append({**item, "id": f"L{number}", "timestamp": f"2026-10-28T02:00:0{number}Z"})
    snapshot = LogSnapshot.model_validate({"service": "checkout", "evidence": list(reversed(evidence))})
    result = get_logs(arguments(), allowed_service="checkout", snapshot=snapshot)
    assert [item["id"] for item in result["evidence"]] == ["L2", "L3", "L4", "L5", "L6"]
    assert result["truncated"] is True


def test_empty_window_is_successful_lookup_not_outage_observation(logs):
    result = get_logs(arguments(start_time="2026-10-28T04:00:00Z", end_time="2026-10-28T04:01:00Z"),
                      allowed_service="checkout", snapshot=logs)
    assert result == {"status": "ok", "service": "checkout", "evidence": [], "truncated": False}


@pytest.mark.parametrize("requested,allowed", [("payments", "checkout"), ("payments", "payments")])
def test_never_returns_another_services_logs(logs, requested, allowed):
    result = get_logs(arguments(service=requested), allowed_service=allowed, snapshot=logs)
    assert result["status"] == "error"
    assert result["evidence"] == []


@pytest.mark.parametrize("kind", ["duplicate", "naive", "not_log", "oversized_content", "too_many"])
def test_rejects_unbounded_or_invalid_snapshots(logs, kind):
    payload = deepcopy(logs.model_dump(mode="json"))
    if kind == "duplicate":
        payload["evidence"][1]["id"] = payload["evidence"][0]["id"]
    elif kind == "naive":
        payload["evidence"][0]["timestamp"] = "2026-10-28T02:00:00"
    elif kind == "not_log":
        payload["evidence"][0]["kind"] = "health"
    elif kind == "oversized_content":
        payload["evidence"][0]["content"] = "x" * 2001
    else:
        payload["evidence"] = [payload["evidence"][0]] * 21
    with pytest.raises(ValueError):
        LogSnapshot.model_validate(payload)


@pytest.mark.parametrize("payload", [b"\xff", b"{}", b" " * 64_001])
def test_loader_rejects_invalid_or_oversized_file(tmp_path, payload):
    path = tmp_path / "logs.json"
    path.write_bytes(payload)
    with pytest.raises(ConfigurationError):
        load_log_snapshot(path)
