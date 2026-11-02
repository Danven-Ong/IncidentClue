"""Evaluation boundaries: corrupted sources, label leakage and explicit live calls."""
import importlib.util
import json
from pathlib import Path
import shutil

import httpx2
from openai import BadRequestError, OpenAI, RateLimitError
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rag_suite", ROOT / "evals/rag/suite.py")
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


@pytest.fixture
def copied_suite(tmp_path):
    shutil.copytree(ROOT / "data/runbooks", tmp_path / "data/runbooks")
    shutil.copytree(ROOT / "evals/rag", tmp_path / "evals/rag")
    return tmp_path, tmp_path / "evals/rag"


def test_corrupted_corpus_refuses_validation(copied_suite):
    root, directory = copied_suite
    (root / "data/runbooks/dns.md").write_text("Replaced source", encoding="utf-8")
    with pytest.raises(AssertionError):
        suite.validate(root, directory)


def test_source_manifest_cannot_escape_allowlist(copied_suite):
    root, directory = copied_suite
    path = root / "data/runbooks/manifest.json"
    manifest = suite.load(path)
    manifest["documents"][0]["path"] = "evals/rag/labels.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AssertionError):
        suite.validate(root, directory)


def test_evaluator_canary_in_input_is_rejected(copied_suite):
    root, directory = copied_suite
    path = directory / "inputs/case-01.json"
    payload = suite.load(path)
    payload["incident"]["summary"] = suite.load(directory / "labels.json")["labels"][0]["canary"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AssertionError):
        suite.validate(root, directory)


def test_fake_baseline_does_not_construct_live_client_or_expose_labels(tmp_path, monkeypatch):
    def no_live(*args, **kwargs):
        pytest.fail("Fake baseline constructed a live client")

    monkeypatch.setattr(suite, "create_groq_client", no_live)
    output = tmp_path / "smoke.jsonl"
    suite.baseline(live=False, model="test-model", split="held_out", limit=12, output=output)
    text = output.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines()]
    assert len(rows) == 13
    assert all(row["status"] == "ok" for row in rows[1:])
    assert all(row["model_attempts"] == 2 for row in rows[1:])
    assert all(len(row["attempts"]) == 2 for row in rows[1:])
    assert all(attempt["status"] == "response_received" and attempt["usage"] is None
               for row in rows[1:] for attempt in row["attempts"])
    assert all(row["summary"]["tool_calls_used"] == 0 for row in rows[1:])
    assert "EVALUATOR_ONLY_RAG" not in text
    assert "relevance" not in text
    with pytest.raises(FileExistsError):
        suite.baseline(live=False, model="test-model", split="held_out", limit=1, output=output)


def test_rate_limit_stops_remaining_cases_without_retry(tmp_path, monkeypatch):
    class RateLimitError(Exception):
        pass

    client = suite.fake_client()
    calls = []

    def limited(**kwargs):
        calls.append(kwargs)
        raise RateLimitError()

    client.chat.completions.create = limited
    monkeypatch.setattr(suite, "create_groq_client", lambda key: client)
    monkeypatch.setenv("GROQ_API_KEY", "fake-test-key")
    output = tmp_path / "limited.jsonl"
    suite.baseline(live=True, model="test-model", split="development", limit=12, output=output)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 1
    assert rows[1]["error_type"] == "RateLimitError"
    assert rows[1]["model_attempts"] == 1
    assert rows[-1]["reason"] == "provider_rate_limit"
    assert len(rows[-1]["remaining_ids"]) == 11
    assert "fake-test-key" not in output.read_text(encoding="utf-8")


def test_actual_sdk_option_copies_preserve_request_and_usage_capture(tmp_path, monkeypatch):
    def respond(request):
        body = json.loads(request.content)
        content = "No lookup requested."
        if "response_format" in body:
            content = json.dumps({"status": "insufficient_evidence", "probable_cause": None,
                "evidence_ids": [], "missing_evidence": ["More observations"], "next_checks": []})
        return httpx2.Response(200, json={"id": "mock", "object": "chat.completion", "created": 1,
            "model": "test-model", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

    with OpenAI(api_key="private-test-key", base_url="https://invalid.example/v1",
                http_client=httpx2.Client(transport=httpx2.MockTransport(respond))) as client:
        monkeypatch.setattr(suite, "create_groq_client", lambda key: client)
        output = tmp_path / "sdk.jsonl"
        suite.baseline(live=True, model="test-model", split="development", limit=1, output=output)
    row = json.loads(output.read_text(encoding="utf-8").splitlines()[1])
    assert row["status"] == "ok"
    assert row["model_attempts"] == len(row["requests"]) == len(row["responses"]) == 2
    assert [usage["total_tokens"] for usage in row["provider_usage"]] == [15, 15]
    assert [attempt["usage"] for attempt in row["attempts"]] == row["provider_usage"]
    assert [attempt["response_index"] for attempt in row["attempts"]] == [0, 1]
    assert "private-test-key" not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("code,category", [
    ("tool_use_failed", "tool_generation_rejected"),
    ("json_validate_failed", "structured_output_rejected"),
    ("context_length_exceeded", "context_limit"),
    ("untrusted-secret-code", "unclassified"),
    (["invalid", "code"], "unclassified"),
])
def test_provider_error_details_exclude_private_text(code, category):
    response = httpx2.Response(400, request=httpx2.Request("POST", "https://invalid.example"),
                              headers={"retry-after": "private-header"})
    error = BadRequestError("private-message", response=response, body={"error": {
        "code": code, "message": "private-message", "failed_generation": "private-generated-text"}})
    assert suite.provider_error_details(error) == {"http_status": 400, "category": category}


def test_rate_limit_metadata_records_numeric_hint_without_retry():
    response = httpx2.Response(429, request=httpx2.Request("POST", "https://invalid.example"),
                              headers={"retry-after": "90"})
    error = RateLimitError("private-message", response=response, body="private-body")
    assert suite.provider_error_details(error) == {
        "http_status": 429, "category": "rate_limit", "retry_after_seconds": 90.0}


@pytest.mark.parametrize("failure", ["rejection", "rate_limit", "timeout"])
def test_failed_final_attempt_is_recorded_without_response_or_usage(tmp_path, monkeypatch, failure):
    calls = []
    private = "private-provider-payload"

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if "response_format" in body:
            if failure == "timeout":
                raise httpx2.ReadTimeout(private, request=request)
            return httpx2.Response(429 if failure == "rate_limit" else 400,
                headers={"retry-after": "70", "private-header": private}, json={"error": {
                    "code": "rate_limit_exceeded" if failure == "rate_limit" else "json_validate_failed",
                    "message": private + " truncation context length token limit",
                    "failed_generation": json.dumps({"status": private, private: private})}})
        message = {"role": "assistant", "content": "done"}
        reason = "stop"
        if len(calls) == 1:
            service = body["tools"][0]["function"]["parameters"]["properties"]["service"]["enum"][0]
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call-health", "type": "function", "function": {
                    "name": "get_service_health", "arguments": json.dumps({"service": service})}}]}
            reason = "tool_calls"
        return httpx2.Response(200, json={"id": "mock", "object": "chat.completion", "created": 1,
            "model": "test-model", "choices": [{"index": 0, "finish_reason": reason, "message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

    with OpenAI(api_key=private, base_url="https://invalid.example/v1",
                http_client=httpx2.Client(transport=httpx2.MockTransport(respond))) as client:
        monkeypatch.setattr(suite, "create_groq_client", lambda key: client)
        output = tmp_path / "failure.jsonl"
        suite.baseline(live=True, model="test-model", split="development", limit=1, output=output)
    text = output.read_text(encoding="utf-8")
    row = json.loads(text.splitlines()[1])
    assert row["status"] == "error"
    assert row["model_attempts"] == len(calls) == len(row["attempts"]) == 3
    assert len(row["responses"]) == len(row["provider_usage"]) == 2
    failed = row["attempts"][-1]
    assert [attempt["phase"] for attempt in row["attempts"]] == ["selection", "selection", "diagnosis"]
    assert failed["status"] == "exception" and failed["usage"] is None
    assert "response_index" not in failed
    assert failed["error_type"] == row["error_type"]
    assert failed["elapsed_seconds"] >= 0
    if failure == "timeout":
        assert "provider_error" not in failed
        assert failed["rejected_generation"] == {"availability": "absent", "truncation": "unknown"}
    else:
        assert failed["provider_error"] == row["provider_error"]
        diagnostic = failed["rejected_generation"]
        assert diagnostic["known_field_types"] == {"status": "string"}
        assert diagnostic["unknown_field_count"] == 1
        assert diagnostic["diagnosis_contract_valid"] is False
        assert diagnostic["truncation"] == "unknown"
    assert private not in text


@pytest.mark.parametrize("generation,expected", [
    (None, {"availability": "not_text"}),
    ({"private": "private"}, {"availability": "not_text"}),
    ('{"private":', {"json_status": "invalid"}),
    ('{"private": NaN}', {"json_status": "invalid"}),
    ('["private"]', {"json_status": "valid", "top_level_type": "array", "diagnosis_contract_valid": False}),
    ('"private"', {"json_status": "valid", "top_level_type": "string", "diagnosis_contract_valid": False}),
    ("private" * 3000, {"json_status": "not_inspected_size_limit"}),
])
def test_rejected_generation_inspection_is_bounded_and_excludes_private_values(generation, expected):
    response = httpx2.Response(400, request=httpx2.Request("POST", "https://invalid.example"))
    error = BadRequestError("private", response=response, body={"failed_generation": generation})
    diagnostic = suite.rejected_generation_details(error, diagnosis=True)
    assert diagnostic.items() >= expected.items()
    assert diagnostic["truncation"] == "unknown"
    assert "private" not in json.dumps(diagnostic)
    assert len(json.dumps(diagnostic)) < 1000


def test_rejected_generation_parser_limit_is_not_mislabeled_as_invalid_json(monkeypatch):
    response = httpx2.Response(400, request=httpx2.Request("POST", "https://invalid.example"))
    error = BadRequestError("private", response=response, body={"failed_generation": "[]"})
    def limit(*args, **kwargs):
        raise RecursionError("private")
    monkeypatch.setattr(suite.json, "loads", limit)
    assert suite.rejected_generation_details(error, diagnosis=True) == {
        "availability": "present", "truncation": "unknown", "character_count": 2,
        "json_status": "not_inspected_parser_limit"}


def test_rejected_generation_contract_check_does_not_assert_cause_or_support(diagnosis_data):
    response = httpx2.Response(400, request=httpx2.Request("POST", "https://invalid.example"))
    # A complete, schema-valid generation can still have been rejected by a provider.
    error = BadRequestError("private", response=response,
                            body={"error": {"failed_generation": json.dumps(diagnosis_data)}})
    diagnostic = suite.rejected_generation_details(error, diagnosis=True)
    assert diagnostic["diagnosis_contract_valid"] is True
    assert diagnostic["known_field_types"]["evidence_ids"] == "array"
    assert diagnostic["truncation"] == "unknown"
    assert diagnosis_data["probable_cause"] not in json.dumps(diagnostic)


def test_recording_client_reraises_same_error_and_ignores_error_keywords():
    response = httpx2.Response(400, request=httpx2.Request("POST", "https://invalid.example"))
    error = BadRequestError("truncated token limit context", response=response, body={"error": {
        "message": "truncated token limit context"}})
    client = suite.fake_client()
    def fail(**request):
        raise error
    client.create = fail
    requests, responses, usage, attempts = [], [], [], []
    recorder = suite.RecordingClient(client, requests, responses, usage, attempts).with_options(max_retries=0)
    with pytest.raises(BadRequestError) as caught:
        recorder.chat.completions.create(model="test-model", messages=[])
    assert caught.value is error
    assert responses == usage == []
    assert attempts[0]["provider_error"]["category"] == "unclassified"
    assert attempts[0]["rejected_generation"] == {"availability": "absent", "truncation": "unknown"}


def test_selection_generation_shape_keeps_only_fixed_names():
    response = httpx2.Response(400, request=httpx2.Request("POST", "https://invalid.example"))
    error = BadRequestError("private", response=response, body={"error": {"failed_generation":
        json.dumps({"name": "private", "arguments": {"private": "private"}, "private": "private"})}})
    diagnostic = suite.rejected_generation_details(error, diagnosis=False)
    assert diagnostic["known_field_types"] == {"name": "string", "arguments": "object"}
    assert diagnostic["unknown_field_count"] == 1
    assert "diagnosis_contract_valid" not in diagnostic
    assert "private" not in json.dumps(diagnostic)


def test_capture_preserves_malformed_sdk_response_for_runtime_validation(tmp_path, monkeypatch):
    def respond(request):
        return httpx2.Response(200, json={"id": "mock", "object": "chat.completion", "created": 1,
            "model": "test-model", "choices": None})
    with OpenAI(api_key="test-key", base_url="https://invalid.example/v1",
                http_client=httpx2.Client(transport=httpx2.MockTransport(respond))) as client:
        monkeypatch.setattr(suite, "create_groq_client", lambda key: client)
        output = tmp_path / "malformed.jsonl"
        suite.baseline(live=True, model="test-model", split="development", limit=1, output=output)
    row = json.loads(output.read_text(encoding="utf-8").splitlines()[1])
    assert row["error_type"] == "DiagnosisError"
    assert row["validation_error"] == "Expected exactly one tool-selection response."
    assert row["responses"] == row["provider_usage"] == [None]
    assert row["attempts"][0]["status"] == "response_received"
    assert row["attempts"][0]["usage"] is None
