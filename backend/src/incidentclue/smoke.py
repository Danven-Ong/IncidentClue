"""Check provider connectivity and strict JSON output with one small request."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from time import perf_counter

from dotenv import load_dotenv
from openai import APIError, OpenAI

from incidentclue.llm import ConfigurationError, DEFAULT_MODEL, create_groq_client


class SmokeCheckError(ValueError):
    """The provider response did not satisfy the connectivity contract."""


def check_connection(client: OpenAI, model: str) -> dict[str, object]:
    """Make one request, verify its payload, and return credential-free metadata."""
    if not model.strip():
        raise ConfigurationError("GROQ_MODEL must not be blank.")

    started_at = datetime.now(timezone.utc).isoformat()
    started = perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": 'Return only the JSON object {"status":"ok"}.',
            },
            {"role": "user", "content": "Report the connectivity status."},
        ],
        max_completion_tokens=512,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "connectivity_status",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string", "enum": ["ok"]}},
                    "required": ["status"],
                    "additionalProperties": False,
                },
            },
        },
    )
    elapsed_ms = round((perf_counter() - started) * 1000, 2)

    if len(response.choices) != 1:
        raise SmokeCheckError("Expected exactly one provider response choice.")
    choice = response.choices[0]
    if choice.finish_reason != "stop":
        raise SmokeCheckError("Provider did not complete the response normally.")
    if choice.message.refusal:
        raise SmokeCheckError("Provider refused the connectivity request.")
    if not choice.message.content:
        raise SmokeCheckError("Provider returned no response content.")

    try:
        payload = json.loads(choice.message.content)
    except json.JSONDecodeError as error:
        raise SmokeCheckError("Provider returned invalid JSON.") from error
    if payload != {"status": "ok"}:
        raise SmokeCheckError("Provider returned an unexpected connectivity payload.")

    return {
        "status": "ok",
        "provider": "groq",
        "requested_model": model,
        "response_model": response.model,
        "response_id": response.id,
        "started_at": started_at,
        "request_latency_ms": elapsed_ms,
        "usage": response.usage.model_dump(exclude_none=True) if response.usage else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", type=Path, help="Explicit local configuration file; optional."
    )
    args = parser.parse_args(argv)

    if args.env_file is not None:
        if not args.env_file.is_file():
            print("The specified environment file does not exist.", file=sys.stderr)
            return 2
        try:
            load_dotenv(args.env_file, override=False, encoding="utf-8-sig")
        except OSError:
            print("Could not read the specified environment file.", file=sys.stderr)
            return 2

    try:
        with create_groq_client(os.environ.get("GROQ_API_KEY", "")) as client:
            result = check_connection(client, os.environ.get("GROQ_MODEL", DEFAULT_MODEL))
    except ConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 2
    except SmokeCheckError as error:
        print(str(error), file=sys.stderr)
        return 1
    except APIError as error:
        status_code = getattr(error, "status_code", None)
        status = f", HTTP {status_code}" if status_code is not None else ""
        print(f"Groq request failed ({type(error).__name__}{status}).", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
