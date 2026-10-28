"""Data models for incident evidence and diagnoses."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Evidence(BaseModel):
    """A timestamped observation used to investigate an incident."""

    id: str = Field(min_length=1)
    timestamp: datetime
    kind: Literal["deployment", "log", "health"]
    content: str = Field(min_length=1)


class Incident(BaseModel):
    """An incident snapshot with uniquely identified evidence records."""

    incident_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence: list[Evidence]

    @field_validator("evidence", mode="after")
    @classmethod
    def validate_unique_evidence_ids(cls, evidence: list[Evidence]) -> list[Evidence]:
        evidence_ids = [record.id for record in evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evidence IDs must be unique within an incident.")
        return evidence


class Diagnosis(BaseModel):
    """A structured investigation result with explicit evidence and uncertainty."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["hypothesis", "insufficient_evidence"]
    probable_cause: str | None
    evidence_ids: list[str]
    missing_evidence: list[str]
    next_checks: list[str]

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        if self.status == "hypothesis":
            if self.probable_cause is None or not self.probable_cause.strip():
                raise ValueError("A hypothesis requires a nonblank probable cause.")
            if not self.evidence_ids:
                raise ValueError("A hypothesis requires at least one evidence ID.")
        elif self.probable_cause is not None:
            raise ValueError("An insufficient-evidence result must have a null cause.")

        if any(not evidence_id.strip() for evidence_id in self.evidence_ids):
            raise ValueError("Evidence IDs must not be blank.")

        return self
