"""Citation membership checks against the incident supplied to an investigation."""

import pytest

from incidentclue.diagnosis import validate_evidence_references
from incidentclue.schemas import Diagnosis, Incident


@pytest.mark.parametrize("references", [["E1"], ["E2"], ["E1", "E2", "E3"]])
def test_accepts_references_from_the_supplied_incident(
    incident_data, diagnosis_data, references
):
    diagnosis_data["evidence_ids"] = references
    incident = Incident.model_validate(incident_data)
    diagnosis = Diagnosis.model_validate(diagnosis_data)
    original_incident = incident.model_dump()
    original_diagnosis = diagnosis.model_dump()

    assert validate_evidence_references(incident, diagnosis) is None
    assert incident.model_dump() == original_incident
    assert diagnosis.model_dump() == original_diagnosis


@pytest.mark.parametrize("references", [["E404"], ["E2", "E404"], ["e2"], [" E2 "]])
def test_rejects_unknown_references(incident_data, diagnosis_data, references):
    diagnosis_data["evidence_ids"] = references
    incident = Incident.model_validate(incident_data)
    diagnosis = Diagnosis.model_validate(diagnosis_data)

    with pytest.raises(ValueError, match="unknown evidence IDs") as error:
        validate_evidence_references(incident, diagnosis)

    assert incident.incident_id in str(error.value)


def test_reports_all_unknown_ids_without_changing_the_diagnosis(
    incident_data, diagnosis_data
):
    diagnosis_data["evidence_ids"] = ["E9", "E404", "E2", "E9"]
    incident = Incident.model_validate(incident_data)
    diagnosis = Diagnosis.model_validate(diagnosis_data)
    original_diagnosis = diagnosis.model_dump()

    with pytest.raises(ValueError) as error:
        validate_evidence_references(incident, diagnosis)

    assert str(error.value).endswith("E404, E9")
    assert diagnosis.model_dump() == original_diagnosis


def test_rejects_reference_only_present_in_another_incident(
    incident_data, diagnosis_data
):
    incident = Incident.model_validate(incident_data)
    incident_data["incident_id"] = "payment-001"
    incident_data["service"] = "payment"
    incident_data["evidence"] = [incident_data["evidence"][0]]
    incident_data["evidence"][0]["id"] = "PAYMENT-E1"
    other_incident = Incident.model_validate(incident_data)
    diagnosis_data["evidence_ids"] = [other_incident.evidence[0].id]
    diagnosis = Diagnosis.model_validate(diagnosis_data)

    with pytest.raises(ValueError, match="PAYMENT-E1"):
        validate_evidence_references(incident, diagnosis)


@pytest.mark.parametrize("references", [[], ["E3"]])
def test_validates_references_in_an_insufficient_evidence_result(
    incident_data, diagnosis_data, references
):
    diagnosis_data.update(
        status="insufficient_evidence", probable_cause=None, evidence_ids=references
    )

    validate_evidence_references(
        Incident.model_validate(incident_data), Diagnosis.model_validate(diagnosis_data)
    )


def test_rejects_unknown_references_even_when_abstaining(incident_data, diagnosis_data):
    diagnosis_data.update(
        status="insufficient_evidence", probable_cause=None, evidence_ids=["E404"]
    )

    with pytest.raises(ValueError, match="E404"):
        validate_evidence_references(
            Incident.model_validate(incident_data), Diagnosis.model_validate(diagnosis_data)
        )


def test_accepts_no_citations_when_incident_has_no_evidence(incident_data, diagnosis_data):
    incident_data["evidence"] = []
    diagnosis_data.update(
        status="insufficient_evidence", probable_cause=None, evidence_ids=[]
    )

    validate_evidence_references(
        Incident.model_validate(incident_data), Diagnosis.model_validate(diagnosis_data)
    )


def test_rejects_a_hypothesis_citation_when_incident_has_no_evidence(
    incident_data, diagnosis_data
):
    incident_data["evidence"] = []

    with pytest.raises(ValueError, match="E2"):
        validate_evidence_references(
            Incident.model_validate(incident_data), Diagnosis.model_validate(diagnosis_data)
        )
