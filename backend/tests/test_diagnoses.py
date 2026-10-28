"""Validation contracts for structured investigation results."""

import json
import pytest
from pydantic import ValidationError

from incidentclue.schemas import Diagnosis


def test_parses_structured_hypothesis_from_json(diagnosis_data):
    diagnosis = Diagnosis.model_validate_json(json.dumps(diagnosis_data))

    assert diagnosis.status == "hypothesis"
    assert diagnosis.probable_cause == diagnosis_data["probable_cause"]
    assert diagnosis.evidence_ids == ["E2"]
    assert diagnosis.missing_evidence == diagnosis_data["missing_evidence"]
    assert diagnosis.next_checks == diagnosis_data["next_checks"]


@pytest.mark.parametrize("evidence_ids", [[], ["E3"]])
def test_accepts_insufficient_evidence_with_null_cause(diagnosis_data, evidence_ids):
    diagnosis_data.update(
        status="insufficient_evidence",
        probable_cause=None,
        evidence_ids=evidence_ids,
    )

    diagnosis = Diagnosis.model_validate(diagnosis_data)

    assert diagnosis.status == "insufficient_evidence"
    assert diagnosis.probable_cause is None
    assert diagnosis.evidence_ids == evidence_ids


@pytest.mark.parametrize(
    "field",
    ["status", "probable_cause", "evidence_ids", "missing_evidence", "next_checks"],
)
def test_requires_every_output_field(diagnosis_data, field):
    diagnosis_data.pop(field)

    with pytest.raises(ValidationError) as error:
        Diagnosis.model_validate(diagnosis_data)

    assert error.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("cause", [None, "", "   "])
def test_rejects_hypothesis_without_meaningful_cause(diagnosis_data, cause):
    diagnosis_data["probable_cause"] = cause

    with pytest.raises(ValidationError, match="requires a nonblank probable cause"):
        Diagnosis.model_validate(diagnosis_data)


def test_rejects_hypothesis_without_citations(diagnosis_data):
    diagnosis_data["evidence_ids"] = []

    with pytest.raises(ValidationError, match="requires at least one evidence ID"):
        Diagnosis.model_validate(diagnosis_data)


@pytest.mark.parametrize("evidence_id", ["", "   "])
def test_rejects_blank_citation_ids(diagnosis_data, evidence_id):
    diagnosis_data["evidence_ids"] = [evidence_id]

    with pytest.raises(ValidationError, match="Evidence IDs must not be blank"):
        Diagnosis.model_validate(diagnosis_data)


@pytest.mark.parametrize("cause", ["Missing configuration.", ""])
def test_rejects_cause_for_insufficient_evidence(diagnosis_data, cause):
    diagnosis_data.update(status="insufficient_evidence", probable_cause=cause)

    with pytest.raises(ValidationError, match="must have a null cause"):
        Diagnosis.model_validate(diagnosis_data)


def test_rejects_unknown_status(diagnosis_data):
    diagnosis_data["status"] = "certain"

    with pytest.raises(ValidationError) as error:
        Diagnosis.model_validate(diagnosis_data)

    assert error.value.errors()[0]["loc"] == ("status",)


def test_rejects_unexpected_output_fields(diagnosis_data):
    diagnosis_data["confidence"] = 0.99

    with pytest.raises(ValidationError) as error:
        Diagnosis.model_validate(diagnosis_data)

    assert error.value.errors()[0]["loc"] == ("confidence",)
    assert error.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize("field", ["evidence_ids", "missing_evidence", "next_checks"])
def test_rejects_null_instead_of_a_list(diagnosis_data, field):
    diagnosis_data[field] = None

    with pytest.raises(ValidationError) as error:
        Diagnosis.model_validate(diagnosis_data)

    assert error.value.errors()[0]["loc"] == (field,)
