"""Public HTTP envelopes; graph snapshots remain data, not HTTP execution inputs."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from incidentclue.diagnosis import MAX_INCIDENT_BYTES
from incidentclue.investigation import initial_investigation_state
from incidentclue.schemas import Incident
from incidentclue.tools import (
    MAX_HEALTH_FILE_BYTES, MAX_LOG_FILE_BYTES, HealthSnapshot, LogSnapshot,
)

PauseBoundary = Literal["decide", "execute", "diagnose", "validate"]


class CreateInvestigation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident: Incident
    health: HealthSnapshot
    logs: LogSnapshot | None = None
    model: str | None = Field(default=None, max_length=200)

    @field_validator("model")
    @classmethod
    def nonblank_model(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Model must be nonblank.")
        return value

    @model_validator(mode="after")
    def bounded_inputs(self):
        for item, limit in ((self.incident, MAX_INCIDENT_BYTES),
                            (self.health, MAX_HEALTH_FILE_BYTES),
                            (self.logs, MAX_LOG_FILE_BYTES)):
            if item is not None and len(item.model_dump_json().encode("utf-8")) > limit:
                raise ValueError("Input exceeds its byte limit.")
        initial_investigation_state(self.incident)
        return self


class Counts(BaseModel):
    model: int = Field(ge=0)
    tool: int = Field(ge=0)


class Attempt(BaseModel):
    operation: str
    kind: Literal["model", "tool"]
    status: Literal["in_flight", "done", "failed"]


class Inspection(BaseModel):
    investigation_id: UUID
    incident_id: str
    status: Literal["ready", "running", "paused", "completed", "failed", "recovery_required"]
    executing: bool
    recovery_needed: bool
    compatible: bool
    pause_requested: bool
    model: str
    error_code: str | None
    checkpoint_id: str | None
    next_nodes: list[str]
    # Inspection must also expose partial/incompatible saved snapshots. Do not
    # require a resumable SavedState merely to read existing data.
    state: dict
    attempts: list[Attempt]
    charged: Counts
    limits: Counts
    created_at: datetime
    updated_at: datetime


class ErrorDetail(BaseModel):
    code: str
    cause: str | None = None
    message: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail
    investigation_id: UUID | None = None


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    database: Literal["ready"] = "ready"
