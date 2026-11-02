"""Investigate a software incident supplied as a JSON file."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from importlib.metadata import version
import os
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

from dotenv import load_dotenv
from openai import APIError
from pydantic import ValidationError

from incidentclue.diagnosis import (
    DIAGNOSIS_INSTRUCTIONS,
    MAX_DIAGNOSIS_TOKENS,
    MAX_INCIDENT_BYTES,
    DiagnosisError,
    InputLimitError,
    diagnose_incident,
)
from incidentclue.llm import (
    DEFAULT_MODEL,
    REQUEST_TIMEOUT_SECONDS,
    ConfigurationError,
    create_groq_client,
)
from incidentclue.records import write_run_record
from incidentclue.schemas import Incident
from incidentclue.investigation import (
    FINAL_INSTRUCTIONS, MAX_MODEL_REQUESTS, MAX_SELECTION_TOKENS, MAX_TOOL_CALLS,
    GRAPH_RECURSION_LIMIT, TOOL_INSTRUCTIONS, investigate_incident,
)
from incidentclue.tools import load_health_snapshot, load_log_snapshot

MAX_INPUT_FILE_BYTES = 64_000


class IncidentFileError(ValueError):
    """The supplied incident file cannot be read or validated."""


def _durable_command(args) -> int:
    from incidentclue.lifecycle import Lifecycle
    from incidentclue.persistence import InvestigationBusy, PersistenceError, open_database

    try:
        _load_environment(args.env_file)
        with open_database(os.environ.get("INCIDENTCLUE_DATABASE_URL", "")) as db:
            if args.command == "db-init":
                db.initialize()
                print(json.dumps({"database": "initialized"}))
                return 0
            service = Lifecycle(db, model_directory=os.environ.get("INCIDENTCLUE_RAG_MODEL_DIR"))
            if args.command == "start":
                incident = _load_incident(args.incident)
                health = load_health_snapshot(args.health_file)
                logs = load_log_snapshot(args.logs_file) if args.logs_file is not None else None
                model = args.model if args.model is not None else os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
                identifier = service.start(incident, health, model, logs=logs, paused=args.paused,
                                           corpus_id=args.runbooks)
                # Print identity before executing: a failure must remain inspectable.
                print(f"Investigation ID: {identifier}", file=sys.stderr, flush=True)
                if args.paused:
                    view = service.inspect(identifier)
                else:
                    view = service.resume(identifier, lambda: create_groq_client(os.environ.get("GROQ_API_KEY", "")),
                                          pause_after=args.pause_after)
            elif args.command == "resume":
                view = service.resume(args.id, lambda: create_groq_client(os.environ.get("GROQ_API_KEY", "")),
                                      pause_after=args.pause_after)
            elif args.command == "pause":
                view = service.pause(args.id)
            else:
                view = service.inspect(args.id)
            print(json.dumps(view, indent=2))
            return 0
    except (IncidentFileError, InputLimitError, ConfigurationError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except InvestigationBusy as error:
        print(str(error), file=sys.stderr)
        return 4
    except PersistenceError as error:
        print(str(error), file=sys.stderr)
        return 1


def _load_incident(path: Path) -> Incident:
    try:
        with path.open("rb") as source:
            payload = source.read(MAX_INPUT_FILE_BYTES + 1)
    except OSError as error:
        raise IncidentFileError("Could not read the incident file.") from error

    if len(payload) > MAX_INPUT_FILE_BYTES:
        raise IncidentFileError(
            f"Incident file exceeds the {MAX_INPUT_FILE_BYTES}-byte file limit."
        )
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise IncidentFileError("Incident file must use UTF-8 encoding.") from error
    try:
        return Incident.model_validate_json(text)
    except ValidationError as error:
        raise IncidentFileError(
            "Incident JSON does not match the required fields or validation rules."
        ) from error


def _load_environment(path: Path | None) -> None:
    if path is None:
        return
    if not path.is_file():
        raise ConfigurationError("The specified environment file does not exist.")
    try:
        load_dotenv(path, override=False, encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise ConfigurationError("Could not read the environment file.") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("diagnose", "Diagnose supplied incident evidence."),
        ("investigate", "Optionally gather synthetic health or log evidence before diagnosis."),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("incident", type=Path)
        command.add_argument("--env-file", type=Path, help="Explicit local configuration file.")
        command.add_argument("--model", help="Override GROQ_MODEL for this investigation.")
        command.add_argument("--record-dir", type=Path, help="Write a local JSON run record.")
        if name == "investigate":
            command.add_argument("--health-file", type=Path, required=True,
                                 help="Explicit synthetic health snapshot; no live probes.")
            command.add_argument("--logs-file", type=Path,
                                 help="Optional synthetic log snapshot; enables get_logs.")
    from incidentclue.lifecycle import NODES
    for name in ("db-init", "start", "inspect", "pause", "resume"):
        command = commands.add_parser(name, help=f"Durable investigation: {name}.")
        command.add_argument("--env-file", type=Path, help="Explicit local configuration file.")
        if name == "start":
            command.add_argument("incident", type=Path)
            command.add_argument("--health-file", type=Path, required=True)
            command.add_argument("--logs-file", type=Path)
            command.add_argument("--model")
            command.add_argument("--runbooks", metavar="CORPUS_SHA256", help="Opt into the exact prepared runbook corpus; requires database and local artifacts on lookup.")
            pause_options = command.add_mutually_exclusive_group()
            pause_options.add_argument("--paused", action="store_true", help="Persist initial state without model calls.")
            pause_options.add_argument("--pause-after", choices=NODES, help="Pause after a committed node.")
        elif name != "db-init":
            command.add_argument("id", help="Stable investigation UUID.")
            if name == "resume":
                command.add_argument("--pause-after", choices=NODES, help="Pause after a committed node.")
    args = parser.parse_args(argv)
    if args.command in ("db-init", "start", "inspect", "pause", "resume"):
        return _durable_command(args)

    started = perf_counter()
    run_id = str(uuid4())
    record: dict[str, object] = {
        "record_version": 1,
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "package_version": version("incidentclue"),
        "sdk_version": version("openai"),
        "provider": "groq",
        "requested_model": None,
        "input": {"file_name": args.incident.name},
        "instructions_sha256": hashlib.sha256(
            DIAGNOSIS_INSTRUCTIONS.encode("utf-8")
        ).hexdigest(),
        "request_policy": {
            "max_file_bytes": MAX_INPUT_FILE_BYTES,
            "max_incident_bytes": MAX_INCIDENT_BYTES,
            "max_completion_tokens": MAX_DIAGNOSIS_TOKENS,
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
        "provider_response_id": None,
        "provider_usage": None,
    }
    record_path = None
    result = None
    error_message = None
    exit_code = 0
    if args.command == "investigate":
        record["mode"] = "investigate"
        record["orchestration"] = "langgraph"
        record["langgraph_version"] = version("langgraph")
        record["graph_steps"] = []
        record["graph_summary"] = {}
        record["instructions_sha256"] = hashlib.sha256(
            FINAL_INSTRUCTIONS.encode("utf-8")
        ).hexdigest()
        record["selection_instructions_sha256"] = hashlib.sha256(
            TOOL_INSTRUCTIONS.encode("utf-8")
        ).hexdigest()
        record["request_policy"].update({
            "max_model_requests": MAX_MODEL_REQUESTS, "max_tool_calls": MAX_TOOL_CALLS,
            "max_selection_tokens": MAX_SELECTION_TOKENS,
            "graph_recursion_limit": GRAPH_RECURSION_LIMIT,
        })
        record["tool_events"] = []

    try:
        if args.record_dir is not None:
            try:
                args.record_dir.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise ConfigurationError(
                    "Could not prepare the run-record directory."
                ) from error
            record_path = args.record_dir / f"{run_id}.json"

        _load_environment(args.env_file)
        model = (
            args.model
            if args.model is not None
            else os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
        )
        record["requested_model"] = model
        incident = _load_incident(args.incident)
        serialized = incident.model_dump_json().encode("utf-8")
        record["input"] = {
            "file_name": args.incident.name,
            "incident_id": incident.incident_id,
            "serialized_bytes": len(serialized),
            "serialized_sha256": hashlib.sha256(serialized).hexdigest(),
        }
        snapshot = None
        logs = None
        if args.command == "investigate":
            snapshot = load_health_snapshot(args.health_file)
            record["health_snapshot"] = {
                "file_name": args.health_file.name,
                "serialized_sha256": hashlib.sha256(
                    snapshot.model_dump_json().encode("utf-8")
                ).hexdigest(),
            }
            if args.logs_file is not None:
                logs = load_log_snapshot(args.logs_file)
                record["log_snapshot"] = {
                    "file_name": args.logs_file.name,
                    "serialized_sha256": hashlib.sha256(
                        logs.model_dump_json().encode("utf-8")
                    ).hexdigest(),
                }
        with create_groq_client(os.environ.get("GROQ_API_KEY", "")) as client:
            if snapshot is None:
                result = diagnose_incident(incident, client, model)
            else:
                result = investigate_incident(
                    incident, client, snapshot, model, events=record["tool_events"], logs=logs,
                    steps=record["graph_steps"], summary=record["graph_summary"],
                )
        record["outcome"] = "success"
        record["diagnosis"] = result.model_dump(mode="json")
    except (IncidentFileError, InputLimitError) as error:
        error_message = str(error)
        record["outcome"] = "input_error"
        exit_code = 2
    except ConfigurationError as error:
        error_message = str(error)
        record["outcome"] = "configuration_error"
        exit_code = 2
    except DiagnosisError as error:
        error_message = str(error)
        record["outcome"] = "diagnosis_error"
        exit_code = 1
    except APIError as error:
        status_code = getattr(error, "status_code", None)
        status = f", HTTP {status_code}" if status_code is not None else ""
        error_message = f"Groq request failed ({type(error).__name__}{status})."
        record["outcome"] = "provider_error"
        record["provider_error_type"] = type(error).__name__
        record["provider_http_status"] = status_code
        exit_code = 1

    record["elapsed_ms_processing"] = round(
        (perf_counter() - started) * 1000, 2
    )
    record["exit_code"] = exit_code
    if error_message is not None:
        record["error"] = error_message
        print(error_message, file=sys.stderr)
    elif result is not None:
        print(result.model_dump_json(indent=2))

    if record_path is not None:
        try:
            write_run_record(record_path, record)
        except OSError:
            print("Could not write the run record.", file=sys.stderr)
            return 3
        print(f"Run record: {record_path}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
