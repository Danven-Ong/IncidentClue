"""Validation contracts for incident snapshots and evidence references."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from incidentclue.schemas import Evidence, Incident


@pytest.fixture
def incident_data() -> dict[str, Any]:
    example = Path(__file__).resolve().parents[2] / "data/synthetic/checkout.json"
    return json.loads(example.read_text(encoding="utf-8-sig"))


def test_validates_example_and_creates_nested_evidence(incident_data):
    incident = Incident.model_validate(incident_data)

    assert incident.incident_id == incident_data["incident_id"]
    assert incident.service == incident_data["service"]
    assert incident.summary == incident_data["summary"]
    assert all(isinstance(record, Evidence) for record in incident.evidence)
    assert [record.id for record in incident.evidence] == [
        record["id"] for record in incident_data["evidence"]
    ]


@pytest.mark.parametrize("field", ["incident_id", "service", "summary", "evidence"])
def test_rejects_missing_incident_field(incident_data, field):
    incident_data.pop(field)

    with pytest.raises(ValidationError) as error:
        Incident.model_validate(incident_data)

    assert error.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("field", ["incident_id", "service", "summary"])
def test_rejects_empty_incident_text(incident_data, field):
    incident_data[field] = ""

    with pytest.raises(ValidationError) as error:
        Incident.model_validate(incident_data)

    assert error.value.errors()[0]["loc"] == (field,)


def test_accepts_explicitly_empty_evidence(incident_data):
    incident_data["evidence"] = []

    incident = Incident.model_validate(incident_data)

    assert incident.evidence == []


def test_rejects_duplicate_evidence_ids(incident_data):
    incident_data["evidence"][-1]["id"] = incident_data["evidence"][0]["id"]

    with pytest.raises(ValidationError) as error:
        Incident.model_validate(incident_data)

    assert error.value.errors()[0]["loc"] == ("evidence",)
    assert "Evidence IDs must be unique" in error.value.errors()[0]["msg"]


def test_reports_invalid_nested_evidence_location(incident_data):
    incident_data["evidence"][1]["kind"] = "banana"

    with pytest.raises(ValidationError) as error:
        Incident.model_validate(incident_data)

    assert error.value.errors()[0]["loc"] == ("evidence", 1, "kind")


def test_rejects_null_evidence(incident_data):
    incident_data["evidence"] = None

    with pytest.raises(ValidationError) as error:
        Incident.model_validate(incident_data)

    assert error.value.errors()[0]["loc"] == ("evidence",)


def test_evidence_ids_can_be_reused_across_incidents(incident_data):
    first = Incident.model_validate(incident_data)
    incident_data["incident_id"] = "checkout-002"

    second = Incident.model_validate(incident_data)

    assert first.incident_id != second.incident_id
    assert first.evidence[0].id == second.evidence[0].id
