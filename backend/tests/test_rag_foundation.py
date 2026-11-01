"""Evaluation boundaries: corrupted sources, label leakage and explicit live calls."""
import importlib.util
import json
from pathlib import Path
import shutil

import httpx2
from openai import OpenAI
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
    assert "private-test-key" not in output.read_text(encoding="utf-8")
