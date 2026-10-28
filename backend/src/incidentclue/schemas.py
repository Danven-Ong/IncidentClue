"""Data models for incident evidence and diagnoses."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
