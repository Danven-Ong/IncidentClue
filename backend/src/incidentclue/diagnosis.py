"""Validation of investigation results against their incident evidence."""

from incidentclue.schemas import Diagnosis, Incident


def validate_evidence_references(incident: Incident, diagnosis: Diagnosis) -> None:
    """Reject citations absent from the supplied incident without changing either object.

    This checks reference membership, not whether evidence supports the diagnosis.
    Both arguments are expected to have passed their Pydantic schema validation.
    """
    available_ids = {record.id for record in incident.evidence}
    unknown_ids = set(diagnosis.evidence_ids) - available_ids

    if unknown_ids:
        references = ", ".join(sorted(unknown_ids))
        raise ValueError(
            f"Diagnosis cites unknown evidence IDs for incident "
            f"{incident.incident_id}: {references}"
        )
