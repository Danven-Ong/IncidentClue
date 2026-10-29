"""Shared durable lifecycle used by the CLI and future application clients."""

from contextlib import nullcontext
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from openai import APIError
import psycopg
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from incidentclue.diagnosis import DiagnosisError, InputLimitError
from incidentclue.investigation import (
    GRAPH_RECURSION_LIMIT, MAX_MODEL_REQUESTS, MAX_TOOL_CALLS,
    build_investigation_graph, initial_investigation_state,
)
from incidentclue.llm import ConfigurationError, DEFAULT_MODEL
from incidentclue.persistence import (
    Database, PersistenceError, RecoveryRequired, investigation_id, json_data,
)
from incidentclue.schemas import Diagnosis, Evidence, Incident
from incidentclue.tools import HealthSnapshot, LogSnapshot

NODES = ("decide", "execute", "diagnose", "validate")


class SavedState(BaseModel):
    """Validate a restored boundary before permitting further execution."""

    model_config = ConfigDict(extra="forbid")
    incident: Incident
    evidence: list[Evidence]
    tool_history: list[dict] = Field(max_length=MAX_TOOL_CALLS)
    messages: list[dict] = Field(min_length=2, max_length=2 + 2 * MAX_TOOL_CALLS)
    tool_calls_used: int = Field(strict=True, ge=0, le=MAX_TOOL_CALLS)
    model_requests_used: int = Field(strict=True, ge=0, le=MAX_MODEL_REQUESTS)
    pending_call: dict | None
    stop_reason: str
    raw_diagnosis: str | None = None
    diagnosis: Diagnosis | None = None

    @model_validator(mode="after")
    def history_matches_budget(self):
        if len(self.tool_history) != self.tool_calls_used:
            raise ValueError("Saved tool history and budget disagree.")
        return self


def runtime_fingerprint() -> dict:
    """Fail closed on changed execution code/dependencies, including installed wheels."""
    sources = sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        sources.update(path.name.encode())
        sources.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return {
        "graph_contract": 1,
        "source_sha256": sources.hexdigest(),
        "dependencies": {name: version(name) for name in (
            "langgraph", "langgraph-checkpoint", "langgraph-checkpoint-postgres",
            "openai", "pydantic", "psycopg",
        )},
        "max_model_requests": MAX_MODEL_REQUESTS,
        "max_tool_calls": MAX_TOOL_CALLS,
    }


def graph_config(identifier: str) -> dict:
    return {"configurable": {"thread_id": identifier}, "recursion_limit": GRAPH_RECURSION_LIMIT}


class Lifecycle:
    def __init__(self, db: Database):
        self.db = db
        db.require_initialized()

    def _graph(self, row: dict, client=None):
        try:
            inputs = row["inputs"]
            health = HealthSnapshot.model_validate(inputs["health"])
            logs = LogSnapshot.model_validate(inputs["logs"]) if inputs["logs"] is not None else None
            model = inputs["model"]
        except (ValidationError, KeyError, TypeError):
            raise PersistenceError("Saved inputs are incompatible or invalid.") from None
        identifier = str(row["id"])
        return build_investigation_graph(
            client, health, model, logs=logs, checkpointer=self.db.saver,
            run_node=lambda name, state, node: self.db.run_node(identifier, name, state, node),
        )

    def _initialize_state(self, row: dict, graph):
        config = graph_config(str(row["id"]))
        snapshot = graph.get_state(config)
        if not snapshot.values:
            if self.db.attempts(str(row["id"])):
                raise RecoveryRequired("Saved graph state is missing after charged attempts.")
            initial = initial_investigation_state(Incident.model_validate(row["inputs"]["incident"]))
            # Save the initial input and START transition, without any physical call.
            graph.invoke(initial, config, interrupt_before=["decide"], durability="sync")
            snapshot = graph.get_state(config)
        return snapshot

    def start(self, incident: Incident, health: HealthSnapshot, model: str = DEFAULT_MODEL,
              *, logs: LogSnapshot | None = None, paused: bool = False) -> str:
        if not model.strip() or len(model) > 200:
            raise ConfigurationError("The model must be nonblank and at most 200 characters.")
        initial_investigation_state(incident)  # Validate bounds before database writes.
        identifier = str(uuid4())
        self.db.create(identifier, {
            "incident": incident, "health": health, "logs": logs, "model": model,
        }, runtime_fingerprint())
        with self.db.execution(identifier):
            row = self.db.get(identifier)
            self._initialize_state(row, self._graph(row))
            if paused:
                self.db.status(identifier, "paused")
        return identifier

    def inspect(self, identifier: str) -> dict:
        identifier = investigation_id(identifier)
        row = self.db.get(identifier)
        graph = self._graph(row)
        state = graph.get_state(graph_config(identifier))
        attempts = self.db.attempts(identifier)
        executing = self.db.is_executing(identifier)
        return {
            "investigation_id": identifier,
            "incident_id": row["inputs"]["incident"]["incident_id"],
            "status": row["status"],
            "executing": executing,
            "recovery_needed": row["status"] == "running" and not executing,
            "compatible": row["runtime"] == runtime_fingerprint(),
            "pause_requested": row["pause_requested"],
            "model": row["inputs"]["model"],
            "error_code": row["error_code"],
            "checkpoint_id": state.config["configurable"].get("checkpoint_id") if state.config else None,
            "next_nodes": list(state.next),
            "state": json_data(state.values),
            "attempts": attempts,
            "charged": {kind: sum(item["kind"] == kind for item in attempts) for kind in ("model", "tool")},
            "limits": {"model": row["runtime"]["max_model_requests"], "tool": row["runtime"]["max_tool_calls"]},
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    def pause(self, identifier: str) -> dict:
        identifier = investigation_id(identifier)
        self.db.request_pause(identifier)
        return self.inspect(identifier)

    def resume(self, identifier: str, client_factory, *, pause_after: str | None = None) -> dict:
        identifier = investigation_id(identifier)
        if pause_after is not None and pause_after not in NODES:
            raise PersistenceError("Pause boundary must name a graph node.")
        with self.db.execution(identifier):
            row = self.db.get(identifier)
            if row["status"] == "completed":
                pass  # Inspect saved output after releasing ownership; never call provider.
            else:
                if row["status"] in ("failed", "recovery_required"):
                    raise PersistenceError("Investigation cannot resume; inspect it and start a new ID.")
                if row["runtime"] != runtime_fingerprint():
                    raise PersistenceError("Saved investigation is incompatible with this runtime; resume refused.")
                attempts = self.db.attempts(identifier)
                if any(item["status"] == "in_flight" for item in attempts):
                    self.db.status(identifier, "recovery_required", "uncertain_attempt")
                    raise RecoveryRequired("An interrupted attempt has an uncertain outcome; resume refused and budget retained.")
                if any(item["status"] == "failed" for item in attempts):
                    self.db.status(identifier, "failed", "prior_attempt_failed")
                    raise PersistenceError("An earlier attempt failed; start a new investigation.")
                try:
                    graph = self._graph(row)
                    state = self._initialize_state(row, graph)
                    if state.next != ("__start__",):
                        SavedState.model_validate(state.values)
                    # Completion may be durable even when its app status update was lost.
                    if not state.next and "diagnosis" in state.values:
                        self.db.status(identifier, "completed")
                    else:
                        self.db.begin(identifier, row["updated_at"])
                        # Client creation is lazy: completed/failed/incompatible IDs
                        # never need provider credentials or a connection to Groq.
                        self._advance(identifier, row, client_factory, pause_after)
                except RecoveryRequired:
                    self.db.status(identifier, "recovery_required", "uncertain_attempt")
                    raise
                except ConfigurationError:
                    # Missing current credentials does not destroy resumable work.
                    self.db.status(identifier, "paused", "configuration_error")
                    raise
                except APIError:
                    self.db.status(identifier, "failed", "provider_error")
                    raise PersistenceError("Provider request failed; investigation is failed and attempts remain charged.") from None
                except (DiagnosisError, InputLimitError, ValidationError):
                    self.db.status(identifier, "failed", "validation_error")
                    raise PersistenceError("Investigation validation failed; inspect the saved progress.") from None
                except PersistenceError:
                    self.db.status(identifier, "recovery_required", "lifecycle_error")
                    raise
                except psycopg.Error:
                    # The outer database boundary sanitizes driver diagnostics.
                    # Do not guess progress if a storage connection has failed.
                    raise
                except Exception:
                    self.db.status(identifier, "failed", "execution_error")
                    raise PersistenceError("Investigation execution failed; inspect the saved progress.") from None
        return self.inspect(identifier)

    def _advance(self, identifier, row, client_factory, pause_after):
        # Enter the client context only once. Database statements before every node
        # and every attempt fail closed if the ownership connection has been lost.
        context = client_factory() if client_factory else nullcontext(None)
        with context as client:
            graph = self._graph(row, client)
            config = graph_config(identifier)
            for _ in range(GRAPH_RECURSION_LIMIT):
                state = graph.get_state(config)
                if state.next != ("__start__",):
                    SavedState.model_validate(state.values)
                if not state.next:
                    if "diagnosis" not in state.values:
                        raise RecoveryRequired("Graph has no next node and no validated diagnosis.")
                    self.db.status(identifier, "completed")
                    return
                if self.db.get(identifier)["pause_requested"]:
                    self.db.status(identifier, "paused")
                    return
                next_node = state.next[0]
                graph.invoke(None, config, interrupt_after="*", durability="sync")
                state = graph.get_state(config)
                if not state.next:
                    self.db.status(identifier, "completed")
                    return
                if next_node == pause_after:
                    self.db.status(identifier, "paused")
                    return
            raise RecoveryRequired("Graph boundary limit exceeded; execution refused.")
