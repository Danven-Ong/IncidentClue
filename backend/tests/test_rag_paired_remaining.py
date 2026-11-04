"""Paired harness integration and execution gates, with no live provider or embedding."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from openai import BadRequestError

from incidentclue.rag.context import RetrievalPin
from incidentclue.rag.corpus import RetrievalConfig, identity, load_corpus
from incidentclue.rag.embeddings import artifact_contract
from incidentclue.rag.storage import bound_sources

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("paired_harness", ROOT / "evals/rag/paired_remaining.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def source_result(corpus):
    passage = next(p for p in corpus.passages if p["document_id"] == "configuration" and p["section"] == "Mechanism")
    return {"status": "ok", **bound_sources(corpus.corpus_id, [{"metadata": passage, "score": 0.1}],
                                            corpus.contract["retrieval"], mode="vector")}


@pytest.fixture
def context():
    corpus = load_corpus(ROOT, artifact_contract(), RetrievalConfig(max_distance=0.3))
    pin = RetrievalPin(corpus_id=corpus.corpus_id, artifact_sha256=identity(artifact_contract()),
                       config_sha256=identity(corpus.contract["retrieval"])).model_dump(mode="json")
    payload = evaluation.baseline.read(ROOT / "evals/rag/inputs/case-01.json")
    return corpus, pin, payload


def test_real_graph_uses_shared_budgets_and_keeps_guidance_out_of_observations(context):
    corpus, pin, payload = context
    seen = []
    def retrieve(incident):
        seen.append(incident)
        return source_result(corpus)
    budget = evaluation.Budget()
    baseline = evaluation.run_case("case-01", "no_rag", payload, evaluation.SmokeClient(),
        "openai/gpt-oss-20b", pin, retrieve, budget)
    rag = evaluation.run_case("case-01", "rag", payload, evaluation.SmokeClient(),
        "openai/gpt-oss-20b", pin, retrieve, budget)
    assert baseline["status"] == rag["status"] == "ok"
    assert baseline["model_attempts"] == rag["model_attempts"] == 3
    assert baseline["tool_attempts"] == 1 and rag["tool_attempts"] == 2
    assert len(seen) == 1 and rag["evidence"] == baseline["evidence"]
    assert baseline["retrieved_sources"] == [] and rag["retrieved_sources"]
    assert rag["diagnosis"]["source_ids"] == [s["id"] for s in rag["retrieved_sources"]]
    assert all("get_runbook_context" not in [t["function"]["name"] for t in r.get("tools", [])] for r in baseline["requests"])
    assert budget.models == 6 and budget.tools == 3 and budget.completion_tokens_reserved == 4096


@pytest.mark.parametrize("failure", ["lookup", "foreign_source", "source_observation"])
def test_retrieval_failures_remain_charged_and_do_not_retry(context, failure):
    corpus, pin, payload = context
    calls = []
    def retrieve(incident):
        calls.append(incident)
        if failure == "lookup":
            raise RuntimeError("private detail must not be printed")
        result = source_result(corpus)
        if failure == "foreign_source":
            result["retrieved_sources"][0]["id"] = "foreign"
        else:
            result["evidence"] = payload["incident"]["evidence"]
        return result
    record = evaluation.run_case("case-01", "rag", payload, evaluation.SmokeClient(),
        "openai/gpt-oss-20b", pin, retrieve, evaluation.Budget())
    assert record["status"] == "error" and record["tool_attempts"] == 1
    assert record["model_attempts"] == 1 and len(calls) == 1
    assert record["retrieval_attempted"] and not record["retrieval_completed"]
    assert "private detail" not in json.dumps(record)


def test_sdk_attempt_error_counts_once_and_preserves_unknown_usage(context):
    _, pin, payload = context
    class Failure(evaluation.SmokeClient):
        def create(self, **request):
            raise RuntimeError("private-provider-value")
    record = evaluation.run_case("case-01", "no_rag", payload, Failure(),
        "openai/gpt-oss-20b", pin, None, evaluation.Budget())
    assert record["status"] == "error" and record["model_attempts"] == 1
    assert record["attempts"][0]["status"] == "exception" and record["attempts"][0]["usage"] is None
    assert "private-provider-value" not in json.dumps(record)


def test_local_global_and_deadline_limits_reserve_before_work():
    clock = [0.0]
    budget = evaluation.Budget(clock=lambda: clock[0])
    local = {"models": 0, "tools": 0}
    for _ in range(3):
        budget.reserve("models", local)
    with pytest.raises(evaluation.BudgetExceeded):
        budget.reserve("models", local)
    assert budget.models == 3
    budget.tools = 10
    with pytest.raises(evaluation.BudgetExceeded):
        budget.reserve("tools", local)
    clock[0] = 1500
    with pytest.raises(evaluation.BudgetExceeded):
        budget.check()


def test_pacing_waits_before_first_and_every_request_without_charging():
    clock, sleeps = [0.0], []
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    budget = evaluation.Budget(clock=lambda: clock[0], sleep=sleep, paced=True)
    budget.wait_for_request()
    budget.wait_for_request()
    assert clock[0] == 130 and max(sleeps) <= 5
    assert budget.models == budget.completion_tokens_reserved == 0
    assert len(budget.pacing_waits) == 2


def test_pacing_refuses_deadline_without_sleep_or_request_charge():
    clock = [1435.0]
    budget = evaluation.Budget(clock=lambda: 0.0, paced=True)
    budget.clock = lambda: clock[0]
    budget.sleep = lambda seconds: pytest.fail("Cannot wait beyond deadline")
    with pytest.raises(evaluation.BudgetExceeded):
        budget.wait_for_request()
    assert budget.models == 0


def test_pacing_oversleep_rechecks_deadline():
    clock = [0.0]
    def sleep(seconds):
        clock[0] = 1500.0
    budget = evaluation.Budget(clock=lambda: clock[0], sleep=sleep, paced=True)
    with pytest.raises(evaluation.BudgetExceeded):
        budget.wait_for_request()
    assert budget.models == 0


def test_fake_mode_never_sleeps():
    budget = evaluation.Budget(sleep=lambda seconds: pytest.fail("Fake mode waited"))
    budget.wait_for_request()
    assert budget.pacing_waits == []


def test_global_request_and_completion_caps_are_lower_than_v1():
    budget = evaluation.Budget()
    for _ in range(5):
        local = {"models": 0, "tools": 0}
        for _ in range(3):
            budget.reserve("models", local)
    with pytest.raises(evaluation.BudgetExceeded):
        budget.reserve("models", {"models": 0, "tools": 0})
    assert budget.models == 15
    budget.completion_tokens_reserved = 10240
    client = evaluation.BoundedClient(None, budget, {"models": 0, "tools": 0})
    with pytest.raises(evaluation.BudgetExceeded):
        client.create(max_completion_tokens=512)
    assert budget.models == 15


def test_live_flag_without_protocol_approval_never_connects_or_reads_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(evaluation.psycopg, "connect", lambda **kw: pytest.fail("Database accessed before approval"))
    monkeypatch.setattr(evaluation, "create_groq_client", lambda key: pytest.fail("Provider accessed before approval"))
    with pytest.raises(SystemExit):
        evaluation.main(["--live", "--model-dir", str(tmp_path), "--output", str(tmp_path / "not-written.jsonl")])
    assert not (tmp_path / "not-written.jsonl").exists()


def prepare_fake_run(monkeypatch):
    backend = SimpleNamespace(artifact=artifact_contract(), model=SimpleNamespace(token_count=len))
    monkeypatch.setattr(evaluation, "LocalEmbedder", lambda path: backend)
    monkeypatch.setattr(evaluation, "prepare_schema", lambda connection: None)
    monkeypatch.setattr(evaluation, "ingest", lambda *args: {"published": False})
    monkeypatch.setattr(evaluation, "make_retriever", lambda connection, corpus, embedder: lambda incident: source_result(corpus))
    monkeypatch.setattr(evaluation, "create_groq_client", lambda key: pytest.fail("Fake mode constructed provider"))


def test_full_fake_pair_schedule_and_review_never_claim_semantic_success(monkeypatch, tmp_path):
    prepare_fake_run(monkeypatch)
    path = tmp_path / "fake.jsonl"
    summary = evaluation.run(None, tmp_path, path, env_file=tmp_path / "absent-secret.env")
    assert summary["all_runs_completed"] and summary["completed_investigations"] == 5
    assert summary["provider_calls"] == 0 and summary["quality_pass"] is None
    assert summary["model_attempts"] == 15 and summary["tool_attempts"] == 7
    spec = importlib.util.spec_from_file_location("paired_quality_review", ROOT / "evals/rag/paired_remaining_review.py")
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    result = review.review(path)
    assert result["quality_pass"] is None and len(result["pairs"]) == 3
    assert all(r["structure_and_citation_membership_valid"] for r in result["results"])
    assert all(r["generation_diagnosis_contract_valid"] is True for r in result["results"])
    # Saved diagnoses have source_ids, but cannot certify the original response.
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    record = next(row for row in rows if row.get("type") == "investigation")
    attempt = next(a for a in record["attempts"] if a["phase"] == "diagnosis")
    message = record["responses"][attempt["response_index"]][0]["message"]
    generated = json.loads(message["content"])
    generated.pop("source_ids")
    message["content"] = json.dumps(generated)
    changed = tmp_path / "missing-generation-field.jsonl"
    changed.write_text("".join(json.dumps(row) + "\n" for row in rows))
    checked = review.review(changed)
    assert checked["results"][0]["structure_and_citation_membership_valid"] is True
    assert checked["results"][0]["generation_diagnosis_contract_valid"] is False
    assert checked["quality_pass"] is None
    assert all(r["usage_unknown_requests"] == 3 for r in result["results"])
    assert any(r["expected_status_matched"] is False for r in result["results"])
    assert "unassessed" in review.worksheet(result, path)
    # Exclusive evidence output must never be overwritten by a second evaluation.
    with pytest.raises(FileExistsError):
        evaluation.run(None, tmp_path, path)


def test_first_execution_error_stops_batch_with_remaining_pairs(monkeypatch, tmp_path):
    prepare_fake_run(monkeypatch)
    original = evaluation.run_case
    count = []
    def fail(*args, **kwargs):
        count.append(1)
        record = original(*args, **kwargs)
        record["status"] = "error"
        record["error_type"] = "SyntheticStopTest"
        return record
    monkeypatch.setattr(evaluation, "run_case", fail)
    path = tmp_path / "partial.jsonl"
    result = evaluation.run(None, tmp_path, path)
    assert not result["all_runs_completed"] and len(count) == 1
    stopped = next(json.loads(line) for line in path.read_text().splitlines() if '"type": "stopped"' in line)
    assert len(stopped["remaining"]) == 4


@pytest.mark.parametrize("kind", ["structured", "validation"])
def test_answer_failure_continues_without_retry_and_counts_failure(monkeypatch, tmp_path, kind):
    prepare_fake_run(monkeypatch)
    class FailedDiagnosis(evaluation.SmokeClient):
        def create(self, **request):
            if "response_format" in request:
                if kind == "validation":
                    raise evaluation.DiagnosisError("invalid diagnosis")
                response = httpx.Response(400, request=httpx.Request("POST", "https://example.invalid"))
                raise BadRequestError("rejected", response=response,
                    body={"error": {"code": "json_validate_failed"}})
            return super().create(**request)
    monkeypatch.setattr(evaluation, "SmokeClient", FailedDiagnosis)
    path = tmp_path / "failed-answers.jsonl"
    summary = evaluation.run(None, tmp_path, path)
    assert summary["all_runs_completed"] and not summary["all_investigations_succeeded"]
    assert summary["failed_investigations"] == 5 and summary["successful_investigations"] == 0
    assert summary["model_attempts"] == 15
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert not any(r["type"] == "stopped" for r in rows)
    assert all(r["failure_scope"] == "answer" for r in rows if r["type"] == "investigation")


@pytest.mark.parametrize("status,code", [(429,"rate_limit_exceeded"), (401,"invalid_api_key"),
                                        (400,"context_length_exceeded"), (500,"unknown")])
def test_provider_errors_outside_allowlist_stop(monkeypatch, tmp_path, status, code):
    prepare_fake_run(monkeypatch)
    class Failure(evaluation.SmokeClient):
        def create(self, **request):
            if "response_format" in request:
                response = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"))
                raise BadRequestError("private", response=response, body={"error": {"code": code}})
            return super().create(**request)
    monkeypatch.setattr(evaluation, "SmokeClient", Failure)
    result = evaluation.run(None, tmp_path, tmp_path / "stop.jsonl")
    assert result["completed_investigations"] == 1 and not result["all_runs_completed"]


def test_selection_and_input_failures_are_not_answer_failures():
    assert not evaluation.answer_failure(evaluation.DiagnosisError("selection"),
                                        [{"node":"decide"}], [])
    assert not evaluation.answer_failure(RuntimeError("unknown"),
                                        [{"node":"diagnose"}], [])
