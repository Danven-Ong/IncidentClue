"""Bounded no-RAG regression of revised instructions; fake by default, no database."""

import argparse
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path

from incidentclue.lifecycle import runtime_fingerprint
from incidentclue.llm import create_groq_client
from incidentclue.schemas import Incident
from incidentclue.tools import HealthSnapshot, LogSnapshot

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROTOCOL = HERE / "investigator-regression-protocol.v1.json"
CASE_IDS = ("case-01", "case-08", "case-10", "case-11", "case-12")
DEPENDENCIES = ("paired_remaining.py", "suite.py", "runbook.py", "representation.py", "retrieval.py")

spec = importlib.util.spec_from_file_location("regression_execution", HERE / "paired_remaining.py")
execution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(execution)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def plan():
    value = execution.baseline.read(PROTOCOL)
    if (value["case_ids"] != list(CASE_IDS) or value["arms"] != ["no_rag"]
            or value["model"] != "openai/gpt-oss-20b"
            or value["caps"] != {"investigations": 5, "models": 15, "tools": 10,
                                 "completion_tokens": 10240, "deadline_seconds": 1500,
                                 "request_timeout_seconds": 20, "wait_seconds": 65}
            or value["runner_sha256"] != digest(Path(__file__))
            or value["dependencies"] != {name: digest(HERE / name) for name in DEPENDENCIES}
            or value["runtime"] != runtime_fingerprint()
            or value["fixture_sha256"] != execution.baseline.verify_frozen()):
        raise ValueError("Regression protocol/source/runtime mismatch")
    inputs = {case: execution.baseline.read(HERE / f"inputs/{case}.json") for case in CASE_IDS}
    if value["input_sha256"] != {case: execution.identity(payload) for case, payload in inputs.items()}:
        raise ValueError("Regression inputs changed")
    for payload in inputs.values():
        Incident.model_validate(payload["incident"])
        HealthSnapshot.model_validate(payload["health"])
        if payload["logs"] is not None:
            LogSnapshot.model_validate(payload["logs"])
    return value, inputs


def run(output, *, live=False, approval=None, env_file=None):
    # Authorization and source checks precede reading credentials or creating output.
    protocol_hash = digest(PROTOCOL)
    if live and approval != protocol_hash:
        raise ValueError("Live execution requires approval of the exact protocol SHA256")
    protocol, inputs = plan()
    if output.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Preserve raw evidence outside application Git")
    with output.open("x", encoding="utf-8") as stream:
        def write(row):
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()

        write({"type": "configuration", "mode": "live_regression" if live else "fake_smoke",
               "protocol": protocol, "protocol_sha256": protocol_hash,
               "quality_pass": None, "held_out_result": "failed, unchanged"})
        client = None
        completed, failures = [], []
        budget = execution.Budget(paced=live)
        try:
            if live:
                if env_file:
                    from dotenv import dotenv_values
                    key = dotenv_values(env_file).get("GROQ_API_KEY", "")
                else:
                    key = os.environ.get("GROQ_API_KEY", "")
                client = create_groq_client(key or "")
            else:
                client = execution.SmokeClient()
            for case in CASE_IDS:
                record = execution.run_case(case, "no_rag", inputs[case], client,
                                            protocol["model"], None, None, budget)
                write(record)
                completed.append(case)
                if record["status"] != "ok":
                    failures.append(case)
                    # Stop even answer-local failures; never inherit v3 continuation.
                    write({"type": "stopped", "reason": "first_execution_error",
                           "remaining": [c for c in CASE_IDS if c not in completed]})
                    break
            summary = {"type": "summary", "mode": "live_regression" if live else "fake_smoke",
                       "attempted_investigations": len(completed),
                       "successful_investigations": len(completed) - len(failures),
                       "failed_investigations": len(failures),
                       "all_investigations_succeeded": len(completed) == 5 and not failures,
                       "model_attempts": budget.models, "tool_attempts": budget.tools,
                       "completion_tokens_reserved": budget.completion_tokens_reserved,
                       "provider_calls": budget.models if live else 0,
                       "quality_pass": None, "rag_benefit_assessed": False,
                       "durable_recovery_assessed": False}
            write(summary)
            return summary
        except Exception as error:
            write({"type": "failure", "error_type": type(error).__name__,
                   "model_attempts": budget.models, "tool_attempts": budget.tools})
            raise
        finally:
            if live and client is not None:
                client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approve-protocol")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    if not args.live:
        execution.baseline.block_external_sockets()
    try:
        summary = run(args.output, live=args.live, approval=args.approve_protocol, env_file=args.env_file)
        print(json.dumps(summary))
        return 0 if summary["all_investigations_succeeded"] else 1
    except Exception as error:
        print(f"Regression stopped ({type(error).__name__}); preserve evidence.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
