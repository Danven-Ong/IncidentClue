"""Data models for incident evidence and diagnoses."""

from datetime import datetime
from typing import Annotated, Literal, Self

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


SupportText = Annotated[str, Field(min_length=1, max_length=240)]


class EvidenceLink(BaseModel):
    """Model-authored explanation tied to an exact supplied observation excerpt."""

    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(min_length=1)
    excerpt: SupportText
    relation: SupportText

    @field_validator("evidence_id", "excerpt", "relation")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Support fields must not be blank.")
        return value


class SupportAssessment(BaseModel):
    """Reviewable model claims; structural validity does not prove causal support."""

    model_config = ConfigDict(extra="forbid")
    evidence_links: list[EvidenceLink] = Field(max_length=3)
    blocking_gaps: list[SupportText] = Field(max_length=3)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if any(not gap.strip() for gap in self.blocking_gaps):
            raise ValueError("Blocking gaps must not be blank.")
        identities = [(link.evidence_id, link.excerpt) for link in self.evidence_links]
        if len(identities) != len(set(identities)):
            raise ValueError("Duplicate evidence links are not allowed.")
        if len(self.model_dump_json().encode("utf-8")) > 2_000:
            raise ValueError("Support assessment exceeds the 2,000-byte limit.")
        return self


class Diagnosis(BaseModel):
    """A structured investigation result with explicit evidence and uncertainty."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["hypothesis", "insufficient_evidence"]
    probable_cause: str | None
    evidence_ids: list[str]
    missing_evidence: list[str]
    next_checks: list[str]
    source_ids: list[str] = Field(default_factory=list)
    support_assessment: SupportAssessment | None = None

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


class GenerationDiagnosis(Diagnosis):
    """New requests require citations and support; legacy saved results may omit them."""
    source_ids: list[str] = Field(...)
    support_assessment: SupportAssessment = Field(...)

    @model_validator(mode="after")
    def validate_declared_support(self) -> Self:
        support = self.support_assessment
        linked_ids = {link.evidence_id for link in support.evidence_links}
        if linked_ids - set(self.evidence_ids):
            raise ValueError("Support links must refer to diagnosis evidence_ids.")
        if self.status == "hypothesis":
            if not linked_ids or linked_ids != set(self.evidence_ids):
                raise ValueError("Every hypothesis evidence ID requires a support link.")
            if support.blocking_gaps:
                raise ValueError("A hypothesis cannot declare blocking gaps.")
        elif not support.blocking_gaps:
            raise ValueError("Abstention requires at least one blocking gap.")
        return self
