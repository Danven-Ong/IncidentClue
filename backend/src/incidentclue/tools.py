"""Allowlisted read-only service health over explicit synthetic snapshots."""

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from incidentclue.llm import ConfigurationError
from incidentclue.schemas import Evidence

MAX_HEALTH_FILE_BYTES = 16_000


class HealthArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    service: str = Field(min_length=1, max_length=80)


class HealthSnapshot(BaseModel):
    """Recorded observations, not live monitoring or a health probe."""

    model_config = ConfigDict(extra="forbid")
    service: str = Field(min_length=1, max_length=80)
    evidence: list[Evidence] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_health(self) -> Self:
        if any(item.kind != "health" or len(item.content) > 2_000 for item in self.evidence):
            raise ValueError("Only bounded health observations are allowed.")
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise ValueError("Health evidence IDs must be unique.")
        return self


def load_health_snapshot(path: Path) -> HealthSnapshot:
    try:
        with path.open("rb") as source:
            payload = source.read(MAX_HEALTH_FILE_BYTES + 1)
        if len(payload) > MAX_HEALTH_FILE_BYTES:
            raise ConfigurationError("Health snapshot exceeds the file limit.")
        return HealthSnapshot.model_validate_json(payload.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValidationError) as error:
        raise ConfigurationError("Could not load a valid synthetic health snapshot.") from error


def get_service_health(
    service: str, *, allowed_service: str, snapshot: HealthSnapshot
) -> dict:
    """Read only the explicitly scoped service; never derive paths from arguments."""
    if service != allowed_service:
        return {"status": "error", "error": "service_not_allowed", "evidence": []}
    if snapshot.service != service or not snapshot.evidence:
        return {"status": "error", "error": "health_unavailable", "evidence": []}
    return {
        "status": "ok", "service": service,
        "evidence": [item.model_dump(mode="json") for item in snapshot.evidence],
    }


HEALTH_TOOL = {
    "type": "function",
    "function": {
        "name": "get_service_health",
        "description": "Read recorded synthetic health observations for the incident service. No changes or live network probes.",
        "parameters": HealthArguments.model_json_schema(),
    },
}
