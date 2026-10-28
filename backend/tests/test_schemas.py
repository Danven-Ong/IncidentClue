"""Validation contracts for incident evidence."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from incidentclue.schemas import Evidence


@pytest.fixture
def evidence_data() -> dict[str, str]:
    return {
        "id": "E2",
        "timestamp": "2026-10-28T02:01:00Z",
        "kind": "log",
        "content": "Checkout startup failed: PAYMENT_API_URL is missing.",
    }


@pytest.mark.parametrize("kind", ["deployment", "log", "health"])
def test_accepts_evidence_and_parses_timestamp(evidence_data, kind):
    evidence_data["kind"] = kind

    evidence = Evidence.model_validate(evidence_data)

    assert evidence.id == "E2"
    assert evidence.timestamp == datetime(2026, 10, 28, 2, 1, tzinfo=timezone.utc)
    assert evidence.kind == kind
    assert evidence.content == evidence_data["content"]


@pytest.mark.parametrize("field", ["id", "timestamp", "kind", "content"])
def test_rejects_missing_required_field(evidence_data, field):
    evidence_data.pop(field)

    with pytest.raises(ValidationError) as error:
        Evidence.model_validate(evidence_data)

    assert error.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", ""),
        ("content", ""),
        ("kind", "banana"),
        ("timestamp", "not-a-timestamp"),
    ],
)
def test_rejects_invalid_field(evidence_data, field, value):
    evidence_data[field] = value

    with pytest.raises(ValidationError) as error:
        Evidence.model_validate(evidence_data)

    assert error.value.errors()[0]["loc"] == (field,)
