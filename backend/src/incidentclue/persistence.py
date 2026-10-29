"""PostgreSQL storage, exclusive execution ownership and charged node attempts."""

from contextlib import contextmanager
from hashlib import sha256
import json
from typing import Callable, Iterator
from uuid import UUID

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from incidentclue.llm import ConfigurationError
from incidentclue.schemas import Diagnosis, Evidence

SCHEMA_VERSION = 1
INIT_LOCK = 470948371522


class PersistenceError(RuntimeError):
    """A controlled database/lifecycle error safe to show in the CLI."""


class InvestigationBusy(PersistenceError):
    pass


class RecoveryRequired(PersistenceError):
    pass


def investigation_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as error:
        raise PersistenceError("Investigation ID must be a UUID.") from error


def lock_key(identifier: str) -> int:
    return int.from_bytes(sha256(identifier.encode()).digest()[:8], "big", signed=True)


def operation_key(name: str, state: dict) -> str:
    return f"{name}:{state['model_requests_used']}:{state['tool_calls_used']}"


def json_data(value):
    """Persist only JSON data; never enable arbitrary object/pickle fallback."""
    def encode(item):
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        raise TypeError("Only validated JSON data may be persisted.")
    return json.loads(json.dumps(value, default=encode))


def restore_update(value: dict) -> dict:
    update = dict(value)
    if "evidence" in update:
        update["evidence"] = [Evidence.model_validate(item) for item in update["evidence"]]
    if "diagnosis" in update:
        update["diagnosis"] = Diagnosis.model_validate(update["diagnosis"])
    return update


@contextmanager
def open_database(url: str) -> Iterator["Database"]:
    if not url or not url.strip():
        raise ConfigurationError("Set INCIDENTCLUE_DATABASE_URL for durable commands.")
    try:
        # Separate connections: the saver may write from LangGraph's worker thread.
        # Session ownership stays on the application connection for its full lifetime.
        with psycopg.connect(url, autocommit=True, row_factory=dict_row,
                             connect_timeout=5, prepare_threshold=0) as app:
            with psycopg.connect(url, autocommit=True, row_factory=dict_row,
                                 connect_timeout=5, prepare_threshold=0) as checkpoints:
                serde = JsonPlusSerializer(
                    pickle_fallback=False, allowed_json_modules=[],
                    allowed_msgpack_modules=[
                        ("incidentclue.schemas", name)
                        for name in ("Incident", "Evidence", "Diagnosis")
                    ],
                )
                yield Database(app, PostgresSaver(checkpoints, serde=serde))
    except psycopg.errors.UndefinedTable:
        raise PersistenceError("Database is not initialized; run db-init first.") from None
    except (psycopg.Error, ValueError):
        # Driver diagnostics can include a DSN/password; do not propagate them.
        raise PersistenceError("PostgreSQL operation failed; check database configuration and readiness.") from None


class Database:
    def __init__(self, connection, saver):
        self.connection = connection
        self.saver = saver
        self.owner: str | None = None

    def initialize(self) -> None:
        self.connection.execute("SELECT pg_advisory_lock(%s)", (INIT_LOCK,))
        try:
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS ic_schema (version integer PRIMARY KEY);
                INSERT INTO ic_schema VALUES (1) ON CONFLICT DO NOTHING;
                CREATE TABLE IF NOT EXISTS ic_investigations (
                    id uuid PRIMARY KEY,
                    status text NOT NULL CHECK (status IN
                        ('ready','running','paused','completed','failed','recovery_required')),
                    pause_requested boolean NOT NULL DEFAULT false,
                    inputs jsonb NOT NULL,
                    runtime jsonb NOT NULL,
                    error_code text,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now()
                );
                CREATE TABLE IF NOT EXISTS ic_attempts (
                    investigation_id uuid NOT NULL REFERENCES ic_investigations(id),
                    operation text NOT NULL,
                    kind text NOT NULL CHECK (kind IN ('model','tool')),
                    status text NOT NULL CHECK (status IN ('in_flight','done','failed')),
                    output jsonb,
                    PRIMARY KEY (investigation_id, operation)
                );
            """, prepare=False)
            self.require_initialized()
            self.saver.setup()
        finally:
            self.connection.execute("SELECT pg_advisory_unlock(%s)", (INIT_LOCK,))

    def require_initialized(self) -> None:
        versions = self.connection.execute("SELECT version FROM ic_schema").fetchall()
        if versions != [{"version": SCHEMA_VERSION}]:
            raise PersistenceError("Database schema version is incompatible; automatic migration is disabled.")

    def create(self, identifier: str, inputs: dict, runtime: dict) -> None:
        self.connection.execute(
            "INSERT INTO ic_investigations (id,status,inputs,runtime) VALUES (%s,'ready',%s,%s)",
            (identifier, Jsonb(json_data(inputs)), Jsonb(runtime)),
        )

    def get(self, identifier: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM ic_investigations WHERE id=%s", (identifier,),
        ).fetchone()
        if row is None:
            raise PersistenceError("Investigation was not found.")
        return row

    @contextmanager
    def execution(self, identifier: str):
        acquired = self.connection.execute(
            "SELECT pg_try_advisory_lock(%s) AS acquired", (lock_key(identifier),),
        ).fetchone()["acquired"]
        if not acquired:
            raise InvestigationBusy("Investigation is already executing in another process.")
        self.owner = identifier
        try:
            yield
        finally:
            self.owner = None
            if not self.connection.closed and not self.connection.broken:
                self.connection.execute("SELECT pg_advisory_unlock(%s)", (lock_key(identifier),))

    def is_executing(self, identifier: str) -> bool:
        # Probe using this fresh command connection. Never called by the owner.
        acquired = self.connection.execute(
            "SELECT pg_try_advisory_lock(%s) AS acquired", (lock_key(identifier),),
        ).fetchone()["acquired"]
        if acquired:
            self.connection.execute("SELECT pg_advisory_unlock(%s)", (lock_key(identifier),))
        return not acquired

    def status(self, identifier: str, status: str, error_code: str | None = None) -> None:
        if self.owner != identifier:
            raise PersistenceError("Execution ownership is required for a lifecycle transition.")
        self.connection.execute(
            "UPDATE ic_investigations SET status=%s,error_code=%s,updated_at=now() WHERE id=%s",
            (status, error_code, identifier),
        )

    def request_pause(self, identifier: str) -> dict:
        # One atomic update; no execution lock needed to request cooperative pause.
        self.get(identifier)
        self.connection.execute("""
            UPDATE ic_investigations SET pause_requested=true,updated_at=now(),
                status=CASE WHEN status='ready' THEN 'paused' ELSE status END
            WHERE id=%s AND status IN ('ready','running','paused')
        """, (identifier,))
        return self.get(identifier)

    def begin(self, identifier: str, expected_updated_at=None) -> None:
        # Clear requests already acknowledged by a previous pause. Requests racing
        # after this update are honored before the next node starts.
        if self.owner != identifier:
            raise PersistenceError("Execution ownership is required.")
        if expected_updated_at is None:
            expected_updated_at = self.get(identifier)["updated_at"]
        self.connection.execute("""
            UPDATE ic_investigations SET status='running',
                pause_requested=CASE WHEN updated_at=%s THEN false ELSE pause_requested END,
                error_code=NULL,updated_at=now() WHERE id=%s
        """, (expected_updated_at, identifier))

    def attempts(self, identifier: str) -> list[dict]:
        return self.connection.execute(
            "SELECT operation,kind,status FROM ic_attempts WHERE investigation_id=%s ORDER BY operation",
            (identifier,),
        ).fetchall()

    def run_node(self, identifier: str, name: str, state: dict, function: Callable) -> dict:
        """Cache committed node output; refuse replay of an uncertain attempt."""
        if self.owner != identifier:
            raise PersistenceError("Execution ownership is required before an attempt.")
        kind = "tool" if name == "execute" else "model"
        operation = operation_key(name, state)
        with self.connection.transaction():
            row = self.connection.execute(
                "SELECT status FROM ic_investigations WHERE id=%s FOR UPDATE", (identifier,),
            ).fetchone()
            if row["status"] != "running":
                raise PersistenceError("Investigation is not running.")
            prior = self.connection.execute(
                "SELECT status,output FROM ic_attempts WHERE investigation_id=%s AND operation=%s",
                (identifier, operation),
            ).fetchone()
            if prior:
                if prior["status"] == "done":
                    return restore_update(prior["output"])
                raise RecoveryRequired("An earlier attempt has no reusable output; execution is refused.")
            used = self.connection.execute(
                "SELECT count(*) AS used FROM ic_attempts WHERE investigation_id=%s AND kind=%s",
                (identifier, kind),
            ).fetchone()["used"]
            from incidentclue.investigation import MAX_MODEL_REQUESTS, MAX_TOOL_CALLS
            limit = MAX_TOOL_CALLS if kind == "tool" else MAX_MODEL_REQUESTS
            if used >= limit:
                raise PersistenceError("Durable attempt budget is exhausted.")
            self.connection.execute(
                "INSERT INTO ic_attempts VALUES (%s,%s,%s,'in_flight',NULL)",
                (identifier, operation, kind),
            )
        # Reservation committed before any physical request. BaseException (kill,
        # KeyboardInterrupt) deliberately leaves an ambiguous charged reservation.
        try:
            update = function(state)
        except Exception:
            self.connection.execute(
                "UPDATE ic_attempts SET status='failed' WHERE investigation_id=%s AND operation=%s",
                (identifier, operation),
            )
            raise
        self.connection.execute(
            "UPDATE ic_attempts SET status='done',output=%s WHERE investigation_id=%s AND operation=%s",
            (Jsonb(json_data(update)), identifier, operation),
        )
        return update
