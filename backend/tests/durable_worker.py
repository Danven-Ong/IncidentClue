"""Offline subprocess fixture for real process death and restart integration tests.

Not an application entry point: the fake provider and crash injection stay in tests.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import time

import httpx2
from openai import OpenAI

from incidentclue.lifecycle import Lifecycle
from incidentclue.persistence import open_database
from incidentclue.schemas import Incident
from incidentclue.tools import load_health_snapshot
from test_investigation import completion


def provider():
    def handler(request):
        body = json.loads(request.content)
        with Path(os.environ["WORKER_REQUESTS"]).open("a", encoding="utf-8") as output:
            output.write("request\n")
        mode = os.environ.get("WORKER_CRASH")
        if mode == "during":
            os._exit(71)
        if mode == "hold":
            Path(os.environ["WORKER_ENTERED"]).touch()
            deadline = time.monotonic() + 20
            while not Path(os.environ["WORKER_RELEASE"]).exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("Worker synchronization timed out.")
                time.sleep(0.05)
        if "response_format" in body:
            message = {"content": json.dumps({
                "status": "hypothesis", "probable_cause": "Missing PAYMENT_API_URL.",
                "evidence_ids": ["H1"], "missing_evidence": ["Deployment configuration."],
                "next_checks": ["Inspect recorded runtime configuration."],
            })}
            reply = completion(message)
        elif any(item["role"] == "tool" for item in body["messages"]):
            reply = completion({"content": "Enough evidence."})
        else:
            reply = completion({"content": None, "tool_calls": [{
                "id": "call-health", "type": "function", "function": {
                    "name": "get_service_health", "arguments": '{"service":"checkout"}',
                },
            }]}, "tool_calls")
        return httpx2.Response(200, json=reply)
    return OpenAI(api_key="test-worker-key", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def main():
    with open_database(os.environ["INCIDENTCLUE_TEST_DATABASE_URL"]) as db:
        service = Lifecycle(db)
        command = sys.argv[1]
        crash = os.environ.get("WORKER_CRASH")
        if crash == "before_initial":
            @contextmanager
            def die_on_execution(identifier):
                print(json.dumps({"investigation_id": identifier}), flush=True)
                os._exit(75)
                yield
            db.execution = die_on_execution
        if command == "start":
            incident = Incident.model_validate_json(Path(sys.argv[2]).read_text(encoding="utf-8"))
            health = load_health_snapshot(Path(sys.argv[3]))
            identifier = service.start(incident, health, "saved-model", paused=True)
        else:
            identifier = sys.argv[2]
        if crash in ("before", "after_output"):
            original = db.run_node
            def crash_node(*args):
                if crash == "before":
                    os._exit(72)
                original(*args)
                os._exit(73)
            db.run_node = crash_node
        if crash == "after_final":
            original_status = db.status
            def status(identifier, value, error_code=None):
                if value == "completed":
                    os._exit(74)
                original_status(identifier, value, error_code)
            db.status = status
        if command == "inspect":
            print(json.dumps(service.inspect(identifier)))
        else:
            boundary = os.environ.get("WORKER_PAUSE_AFTER") or None
            print(json.dumps(service.resume(identifier, provider, pause_after=boundary)))


if __name__ == "__main__":
    main()
