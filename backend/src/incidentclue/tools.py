"""Allowlisted read-only health and log access over explicit synthetic snapshots."""

from pathlib import Path
from datetime import timedelta
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from incidentclue.llm import ConfigurationError
from incidentclue.schemas import Evidence

MAX_HEALTH_FILE_BYTES = 16_000
MAX_LOG_FILE_BYTES = 64_000
MAX_RETURNED_LOGS = 5
MAX_LOG_WINDOW = timedelta(hours=1)


class LogArguments(BaseModel):
    """Explicit service and inclusive, timezone-aware query window."""

    model_config = ConfigDict(extra="forbid", strict=True)
    service: str = Field(min_length=1, max_length=80)
    start_time: AwareDatetime
    end_time: AwareDatetime

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if not timedelta(0) < self.end_time - self.start_time <= MAX_LOG_WINDOW:
            raise ValueError("Log window must be positive and at most one hour.")
        return self


class LogSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str = Field(min_length=1, max_length=80)
    evidence: list[Evidence] = Field(max_length=20)

    @model_validator(mode="after")
    def validate_logs(self) -> Self:
        if any(item.kind != "log" or len(item.content) > 2_000
               or item.timestamp.utcoffset() is None for item in self.evidence):
            raise ValueError("Only bounded, timezone-aware log observations are allowed.")
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise ValueError("Log evidence IDs must be unique.")
        return self


def load_log_snapshot(path: Path) -> LogSnapshot:
    try:
        with path.open("rb") as source:
            payload = source.read(MAX_LOG_FILE_BYTES + 1)
        if len(payload) > MAX_LOG_FILE_BYTES:
            raise ConfigurationError("Log snapshot exceeds the file limit.")
        return LogSnapshot.model_validate_json(payload.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValidationError) as error:
        raise ConfigurationError("Could not load a valid synthetic log snapshot.") from error


def get_logs(args: LogArguments, *, allowed_service: str, snapshot: LogSnapshot) -> dict:
    if args.service != allowed_service:
        return {"status": "error", "error": "service_not_allowed", "evidence": []}
    if snapshot.service != args.service:
        return {"status": "error", "error": "logs_unavailable", "evidence": []}
    matches = sorted(
        (item for item in snapshot.evidence if args.start_time <= item.timestamp <= args.end_time),
        key=lambda item: (item.timestamp, item.id),
    )
    return {
        "status": "ok", "service": args.service,
        "evidence": [item.model_dump(mode="json") for item in matches[-MAX_RETURNED_LOGS:]],
        "truncated": len(matches) > MAX_RETURNED_LOGS,
    }


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

LOG_TOOL = {
    "type": "function",
    "function": {
        "name": "get_logs",
        "description": "Read up to five latest synthetic application logs for the incident service in an inclusive timezone-aware window of at most one hour. Empty results mean no matches in this snapshot, not proof of no errors. No changes or live queries.",
        "parameters": LogArguments.model_json_schema(),
    },
}
