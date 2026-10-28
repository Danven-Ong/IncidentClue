"""Investigate a software incident supplied as a JSON file."""

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
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

MAX_INPUT_FILE_BYTES = 64_000


class IncidentFileError(ValueError):
    """The supplied incident file cannot be read or validated."""


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
    diagnose = commands.add_parser("diagnose", help="Diagnose an incident JSON file.")
    diagnose.add_argument("incident", type=Path)
    diagnose.add_argument("--env-file", type=Path, help="Explicit local configuration file.")
    diagnose.add_argument("--model", help="Override GROQ_MODEL for this investigation.")
    diagnose.add_argument("--record-dir", type=Path, help="Write a local JSON run record.")
    args = parser.parse_args(argv)

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
        with create_groq_client(os.environ.get("GROQ_API_KEY", "")) as client:
            result = diagnose_incident(incident, client, model)
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
