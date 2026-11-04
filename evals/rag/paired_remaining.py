"""Frozen remaining-arm v3 diagnostic snapshot; v1 runner and stopped evidence are preserved."""

import argparse
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import psycopg
from psycopg.rows import dict_row
from openai.types.chat import ChatCompletionMessage

from incidentclue.investigation import (
    GRAPH_RECURSION_LIMIT, build_investigation_graph, initial_investigation_state,
)
from incidentclue.diagnosis import DiagnosisError
from incidentclue.lifecycle import runtime_fingerprint
from incidentclue.llm import create_groq_client
from incidentclue.rag.context import RetrievalPin, construct_query
from incidentclue.rag.corpus import RetrievalConfig, identity, load_corpus
from incidentclue.rag.embeddings import LocalEmbedder, artifact_contract
from incidentclue.rag.storage import ingest, prepare_schema
from incidentclue.schemas import Incident
from incidentclue.tools import HealthSnapshot, LogSnapshot

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROTOCOL = HERE / "paired-protocol.v3.json"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


policy = module("paired_runbook_v3", HERE / "runbook.py")
suite = module("paired_capture", HERE / "suite.py")
baseline = policy.baseline
CASE_IDS = ("case-10", "case-11", "case-12")


class BudgetExceeded(RuntimeError):
    pass


def plan():
    value = baseline.read(PROTOCOL)
    if (tuple(value["case_ids"]) != CASE_IDS or value["model"] != "openai/gpt-oss-20b"
            or value["pacing"]["wait_before_every_request_seconds"] != 65
            or value["pacing"]["sleep_chunk_seconds"] != 5
            or value["maximum_completion_tokens"] != 10240
            or value["schedule"] != [list(row) for row in schedule()]
            or (value["maximum_model_requests"], value["maximum_tool_attempts"],
                value["maximum_investigations"], value["batch_start_deadline_seconds"]) != (15, 10, 5, 1500)):
        raise ValueError("Unsupported paired protocol")
    return value


class Budget:
    def __init__(self, clock=time.monotonic, sleep=time.sleep, paced=False):
        self.clock, self.deadline = clock, clock() + 1500
        self.models = self.tools = 0
        self.completion_tokens_reserved = 0
        self.sleep, self.paced = sleep, paced
        self.pacing_waits = []

    def check(self):
        if self.clock() >= self.deadline:
            raise BudgetExceeded("Batch deadline reached")

    def wait_for_request(self):
        if not self.paced:
            return
        started = self.clock()
        ready = started + 65
        if ready >= self.deadline:
            raise BudgetExceeded("Insufficient time for paced request before deadline")
        while self.clock() < ready:
            self.check()
            self.sleep(min(5, ready - self.clock()))
        self.check()
        self.pacing_waits.append({"wait_seconds": self.clock() - started})

    def reserve(self, kind, local):
        self.check()
        limit, cap = (15, 3) if kind == "models" else (10, 2)
        if getattr(self, kind) >= limit or local[kind] >= cap:
            raise BudgetExceeded("Attempt budget exhausted")
        setattr(self, kind, getattr(self, kind) + 1)
        local[kind] += 1


class BoundedClient:
    def __init__(self, recording, budget, local):
        self.recording, self.budget, self.local = recording, budget, local
        self.chat = SimpleNamespace(completions=self)

    def with_options(self, **options):
        return BoundedClient(self.recording.with_options(**options), self.budget, self.local)

    def create(self, **request):
        tokens = request.get("max_completion_tokens")
        limit = 1024 if "response_format" in request else 512
        if (type(tokens) is not int or not 0 < tokens <= limit
                or self.budget.completion_tokens_reserved + tokens > 10240):
            raise BudgetExceeded("Completion token budget exceeded")
        self.budget.wait_for_request()
        self.budget.reserve("models", self.local)
        self.budget.completion_tokens_reserved += tokens
        return self.recording.chat.completions.create(**request)


class SmokeClient:
    """Scripted transport: exercise tools/citations only, never predict incident answers."""
    def __init__(self):
        self.chat = SimpleNamespace(completions=self)

    def with_options(self, **options):
        return self

    def create(self, **request):
        tools = {t["function"]["name"] for t in request.get("tools", [])}
        previous = [m for m in request["messages"] if m["role"] == "assistant"]
        used = {c["function"]["name"] for m in previous for c in m.get("tool_calls", [])}
        if "response_format" in request:
            sources = [s["id"] for m in request["messages"] if m["role"] == "tool"
                       for s in json.loads(m["content"]).get("retrieved_sources", [])]
            message = ChatCompletionMessage(role="assistant", content=json.dumps({
                "status": "insufficient_evidence", "probable_cause": None, "evidence_ids": [],
                "source_ids": sources, "missing_evidence": ["Fake harness smoke; no quality judgment"],
                "support_assessment": {"evidence_links": [], "blocking_gaps": ["Additional observations are required; scripted response."]},
                    "next_checks": ["Inspect matching observations; smoke output only"]}))
            reason = "stop"
        else:
            name = next((n for n in ("get_runbook_context", "get_service_health") if n in tools and n not in used), None)
            if name:
                definition = next(t for t in request["tools"] if t["function"]["name"] == name)
                service = definition["function"]["parameters"]["properties"]["service"]["enum"][0]
                message = ChatCompletionMessage(role="assistant", content=None, tool_calls=[{
                    "id": f"smoke-{len(previous)}", "type": "function", "function": {
                        "name": name, "arguments": json.dumps({"service": service})}}])
                reason = "tool_calls"
            else:
                message, reason = ChatCompletionMessage(role="assistant", content="done"), "stop"
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=reason)], usage=None)


def schedule():
    return [("case-10", "no_rag"), ("case-11", "no_rag"),
            ("case-11", "rag"), ("case-12", "rag"), ("case-12", "no_rag")]


def answer_failure(error, charges, attempts):
    # Narrow allowlist: unknown/transport/retrieval/selection errors stop the batch.
    last = charges[-1] if charges else {}
    diagnosis_phase = last.get("node") == "diagnose"
    if not diagnosis_phase:
        return False
    details = suite.provider_error_details(error)
    if details == {"http_status": 400, "category": "structured_output_rejected"}:
        return bool(attempts and attempts[-1].get("phase") == "diagnosis")
    return type(error) is DiagnosisError


def make_retriever(connection, corpus, embedder):
    def retrieve(incident):
        query, info = construct_query(incident, embedder.shared.backend.model.token_count)
        return {"status": "ok", **policy.runbook_lookup(connection, corpus, incident.service, query, embedder), "query": info}
    return retrieve


def run_case(case_id, arm, payload, client, model, pin, retrieve, budget):
    budget.check()
    incident = Incident.model_validate(payload["incident"])
    health = HealthSnapshot.model_validate(payload["health"])
    logs = LogSnapshot.model_validate(payload["logs"]) if payload["logs"] else None
    requests, responses, usage, attempts, events, steps, charges = [], [], [], [], [], [], []
    local = {"models": 0, "tools": 0}
    recording = suite.RecordingClient(client, requests, responses, usage, attempts)
    bounded = BoundedClient(recording, budget, local)
    saved = {"state": initial_investigation_state(incident, pin if arm == "rag" else None)}

    def guard(name, state, node):
        budget.check()
        saved["state"] = deepcopy(state)
        if name == "execute":
            budget.reserve("tools", local)
        charge = {"node": name, "status": "started"}
        if name == "execute":
            charge["tool_name"] = state["pending_call"]["name"]
        charges.append(charge)
        try:
            update = node(state)
            charge["status"] = "completed"
            return update
        except Exception as error:
            charge.update(status="failed", error_type=type(error).__name__)
            raise

    record = {"type": "investigation", "case_id": case_id, "arm": arm,
              "input_sha256": identity(payload)}
    started = time.perf_counter()
    try:
        graph = build_investigation_graph(bounded, health, model, logs=logs, events=events,
            steps=steps, run_node=guard, retrieval=pin if arm == "rag" else None,
            runbook_lookup=retrieve if arm == "rag" else None)
        state = graph.invoke(saved["state"], {"recursion_limit": GRAPH_RECURSION_LIMIT})
        saved["state"] = state
        record.update(status="ok", diagnosis=state["diagnosis"].model_dump(mode="json"))
    except Exception as error:
        record.update(status="error", error_type=type(error).__name__,
                      failure_scope="answer" if answer_failure(error, charges, attempts) else "batch")
    state = saved["state"]
    record.update(elapsed_seconds=time.perf_counter() - started, model_attempts=local["models"],
        tool_attempts=local["tools"], requests=requests, responses=responses, attempts=attempts,
        provider_usage=usage, events=events, steps=steps, node_attempts=charges,
        evidence=[e.model_dump(mode="json") for e in state["evidence"]],
        retrieved_sources=state.get("retrieved_sources", []),
        retrieval_attempted=any(c.get("tool_name") == "get_runbook_context" for c in charges),
        retrieval_completed=any(e.get("name") == "get_runbook_context" for e in events),
        pacing_waits=list(budget.pacing_waits), quality_pass=None)
    return record


def run(connection, model_directory, output, *, live=False, approval=None, env_file=None):
    if live:
        raise ValueError("Historical paired protocol does not authorize the new support contract")
    protocol = plan()
    protocol_hash = sha256(PROTOCOL.read_bytes()).hexdigest()
    if live and approval != protocol_hash:
        raise ValueError("Live execution needs exact protocol approval")
    if output.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Store raw evaluation evidence outside application Git")
    fixture = baseline.verify_frozen()
    selected = baseline.read(HERE / "calibration-development.v3.json")
    if (selected["summary"]["selected_cutoff"] != 0.3
            or sha256((HERE / "runbook.py").read_bytes()).hexdigest() != selected["runner_sha256"]
            or any(sha256((HERE / name).read_bytes()).hexdigest() != digest
                   for name, digest in selected["helper_sha256"].items())):
        raise ValueError("Experimental policy changed from frozen v3")
    with output.open("x", encoding="utf-8") as stream:
        def write(row):
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
        write({"type": "configuration", "mode": "live_diagnostic" if live else "fake_smoke",
               "protocol": protocol, "protocol_sha256": protocol_hash,
               "runner_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
               "fixture_sha256": fixture, "runtime": runtime_fingerprint(),
               "policy_sha256": sha256((HERE / "runbook.py").read_bytes()).hexdigest(),
               "capture_sha256": sha256((HERE / "suite.py").read_bytes()).hexdigest(),
               "quality_pass": None, "held_out_result": "failed; not superseded by this diagnostic"})
        client = None
        budget, completed, failed = Budget(paced=live), [], []
        try:
            backend = LocalEmbedder(model_directory)
            shared = baseline.CachedEmbedder(backend)
            base = load_corpus(ROOT, artifact_contract(), RetrievalConfig(max_distance=0.3))
            embedder = policy.control.RepresentationEmbedder(shared, base.passages, "title_context")
            corpus = load_corpus(ROOT, embedder.artifact, RetrievalConfig(max_distance=0.3))
            prepare_schema(connection)
            write({"type": "publication", **ingest(connection, corpus, embedder)})
            pin = RetrievalPin(corpus_id=corpus.corpus_id, artifact_sha256=identity(embedder.artifact),
                               config_sha256=identity(corpus.contract["retrieval"])).model_dump(mode="json")
            retrieve = make_retriever(connection, corpus, embedder)
            # Validate only the selected inputs; no evaluator labels enter graph/client setup.
            inputs = {c: baseline.read(HERE / f"inputs/{c}.json") for c in CASE_IDS}
            for payload in inputs.values():
                Incident.model_validate(payload["incident"])
                HealthSnapshot.model_validate(payload["health"])
                if payload["logs"]:
                    LogSnapshot.model_validate(payload["logs"])
            if live:
                if env_file:
                    from dotenv import dotenv_values
                    key = dotenv_values(env_file).get("GROQ_API_KEY", "")
                else:
                    key = os.environ.get("GROQ_API_KEY", "")
                client = create_groq_client(key or "")
            else:
                client = SmokeClient()
            for case, arm in schedule():
                record = run_case(case, arm, inputs[case], client, protocol["model"], pin, retrieve, budget)
                write(record)
                completed.append((case, arm))
                if record["status"] != "ok":
                    failed.append((case, arm))
                if record["status"] != "ok" and record.get("failure_scope") != "answer":
                    write({"type": "stopped", "reason": "first_investigation_error", "remaining": [r for r in schedule() if r not in completed]})
                    break
            summary = {"type": "summary", "mode": "live_diagnostic" if live else "fake_smoke",
                       "completed_investigations": len(completed), "planned_investigations": 5,
                       "successful_investigations": len(completed) - len(failed),
                       "failed_investigations": len(failed),
                       "model_attempts": budget.models, "tool_attempts": budget.tools,
                       "completion_tokens_reserved": budget.completion_tokens_reserved,
                       "provider_calls": budget.models if live else 0, "quality_pass": None,
                       "all_runs_completed": len(completed) == 5,
                       "all_investigations_succeeded": len(completed) == 5 and not failed,
                       "runtime_promoted": False, "durable_recovery_assessed": False}
            write(summary)
            return summary
        except Exception as error:
            write({"type": "failure", "error_type": type(error).__name__, "completed": completed,
                   "model_attempts": budget.models, "tool_attempts": budget.tools})
            raise
        finally:
            if live and client is not None:
                client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approve-protocol")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    # Validate authorization before connection or acquisition of any credential.
    if args.live and args.approve_protocol != sha256(PROTOCOL.read_bytes()).hexdigest():
        parser.error("Live mode requires approval of the exact paired protocol hash")
    if args.live:
        parser.error("Historical paired protocol does not authorize the new support contract")
    if not args.live:
        baseline.block_external_sockets()
    try:
        parameters = baseline.connection_parameters(os.environ.get("INCIDENTCLUE_RAG_TEST_DATABASE_URL", ""))
        with psycopg.connect(**parameters, autocommit=True, row_factory=dict_row) as connection:
            result = run(connection, args.model_dir, args.output, live=args.live,
                         approval=args.approve_protocol, env_file=args.env_file)
        print(json.dumps(result))
        return 0 if result["all_investigations_succeeded"] else 1
    except Exception as error:
        print(f"Paired evaluation failed ({type(error).__name__}); preserve partial evidence.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
