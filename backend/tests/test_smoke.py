"""Provider request and failure handling with the real SDK and a fake transport."""

from copy import deepcopy
import json

import httpx2
import pytest
from openai import APIStatusError, APITimeoutError, OpenAI

from incidentclue import smoke
from incidentclue.llm import (
    ConfigurationError,
    DEFAULT_MODEL,
    GROQ_BASE_URL,
    REQUEST_TIMEOUT_SECONDS,
    create_groq_client,
)


@pytest.fixture(autouse=True)
def isolated_provider_environment(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)


@pytest.fixture
def completion():
    return {
        "id": "chatcmpl-smoke-test",
        "object": "chat.completion",
        "created": 1,
        "model": DEFAULT_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": '{"status":"ok"}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
    }


def transport_client(handler):
    return OpenAI(
        api_key="test-groq-key",
        base_url=GROQ_BASE_URL,
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def test_client_has_explicit_provider_timeout_and_no_retries():
    with create_groq_client(" test-groq-key ") as client:
        assert str(client.base_url).rstrip("/") == GROQ_BASE_URL
        assert client.api_key == "test-groq-key"
        assert client.timeout == REQUEST_TIMEOUT_SECONDS
        assert client.max_retries == 0


@pytest.mark.parametrize("key", ["", "   "])
def test_client_rejects_missing_key(key):
    with pytest.raises(ConfigurationError, match="Set GROQ_API_KEY"):
        create_groq_client(key)


def test_sends_one_bounded_strict_json_request_and_returns_metadata(completion):
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == GROQ_BASE_URL + "/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == DEFAULT_MODEL
        assert body["max_completion_tokens"] == 512
        schema = body["response_format"]["json_schema"]
        assert schema["strict"] is True
        assert schema["schema"]["additionalProperties"] is False
        return httpx2.Response(200, json=completion)

    with transport_client(handler) as client:
        result = smoke.check_connection(client, DEFAULT_MODEL)

    assert len(requests) == 1
    assert result["status"] == "ok"
    assert result["response_model"] == DEFAULT_MODEL
    assert result["response_id"] == "chatcmpl-smoke-test"
    assert result["usage"]["total_tokens"] == 25
    assert result["request_latency_ms"] >= 0
    assert "test-groq-key" not in json.dumps(result)


@pytest.mark.parametrize(
    ("content", "expected_error"),
    [
        (None, "no response content"),
        ("", "no response content"),
        ("not-json", "invalid JSON"),
        ('{"status":"wrong"}', "unexpected connectivity payload"),
        ('{"status":"ok","extra":true}', "unexpected connectivity payload"),
    ],
)
def test_rejects_unusable_response_content(completion, content, expected_error):
    completion["choices"][0]["message"]["content"] = content

    with transport_client(lambda request: httpx2.Response(200, json=completion)) as client:
        with pytest.raises(smoke.SmokeCheckError, match=expected_error):
            smoke.check_connection(client, DEFAULT_MODEL)


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls"])
def test_rejects_incomplete_responses(completion, finish_reason):
    completion["choices"][0]["finish_reason"] = finish_reason

    with transport_client(lambda request: httpx2.Response(200, json=completion)) as client:
        with pytest.raises(smoke.SmokeCheckError, match="did not complete"):
            smoke.check_connection(client, DEFAULT_MODEL)


def test_rejects_refusal(completion):
    completion["choices"][0]["message"]["refusal"] = "Request refused."

    with transport_client(lambda request: httpx2.Response(200, json=completion)) as client:
        with pytest.raises(smoke.SmokeCheckError, match="refused"):
            smoke.check_connection(client, DEFAULT_MODEL)


@pytest.mark.parametrize("choice_count", [0, 2])
def test_rejects_unexpected_choice_count(completion, choice_count):
    completion["choices"] = [deepcopy(completion["choices"][0]) for _ in range(choice_count)]

    with transport_client(lambda request: httpx2.Response(200, json=completion)) as client:
        with pytest.raises(smoke.SmokeCheckError, match="exactly one"):
            smoke.check_connection(client, DEFAULT_MODEL)


@pytest.mark.parametrize("status", [401, 429, 500])
def test_provider_errors_do_not_retry(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(status, json={"error": {"message": "Provider failure."}})

    with transport_client(handler) as client:
        with pytest.raises(APIStatusError):
            smoke.check_connection(client, DEFAULT_MODEL)

    assert len(calls) == 1


def test_timeout_is_reported_by_the_sdk_without_retrying():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx2.ReadTimeout("Simulated timeout.", request=request)

    with transport_client(handler) as client:
        with pytest.raises(APITimeoutError):
            smoke.check_connection(client, DEFAULT_MODEL)

    assert len(calls) == 1


def test_main_reports_missing_key_without_a_traceback(capsys):
    assert smoke.main([]) == 2
    output = capsys.readouterr()
    assert "Set GROQ_API_KEY" in output.err
    assert output.out == ""
    assert "Traceback" not in output.err


def test_main_reports_missing_env_file(tmp_path, capsys):
    assert smoke.main(["--env-file", str(tmp_path / "missing.env")]) == 2
    assert "does not exist" in capsys.readouterr().err


@pytest.mark.parametrize("existing_key", [None, "environment-key"])
def test_main_loads_explicit_env_file_without_overriding_environment(
    tmp_path, monkeypatch, completion, capsys, existing_key
):
    env_file = tmp_path / "provider.env"
    env_file.write_text("GROQ_API_KEY=file-key\nGROQ_MODEL=" + DEFAULT_MODEL)
    if existing_key is not None:
        monkeypatch.setenv("GROQ_API_KEY", existing_key)
    received_keys = []

    def create_client(key):
        received_keys.append(key)
        return transport_client(lambda request: httpx2.Response(200, json=completion))

    monkeypatch.setattr(smoke, "create_groq_client", create_client)

    assert smoke.main(["--env-file", str(env_file)]) == 0
    output = capsys.readouterr()
    assert received_keys == [existing_key or "file-key"]
    assert json.loads(output.out)["status"] == "ok"
    assert "environment-key" not in output.out
    assert "file-key" not in output.out


def test_main_sanitizes_provider_errors(monkeypatch, capsys):
    monkeypatch.setenv("GROQ_API_KEY", "private-test-key")
    monkeypatch.setattr(
        smoke,
        "create_groq_client",
        lambda key: transport_client(
            lambda request: httpx2.Response(
                401, json={"error": {"message": "Sensitive provider details."}}
            )
        ),
    )

    assert smoke.main([]) == 1
    output = capsys.readouterr()
    assert "AuthenticationError, HTTP 401" in output.err
    assert "Sensitive provider details" not in output.err
    assert "private-test-key" not in output.err
    assert output.out == ""
