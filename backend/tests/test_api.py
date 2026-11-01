"""HTTP boundary validation and sanitization without PostgreSQL or provider calls."""

import asyncio
from contextlib import contextmanager
import json

from fastapi.testclient import TestClient
import pytest

from incidentclue.api.app import MAX_BODY_BYTES, Settings, create_app
from incidentclue.api.schemas import CreateInvestigation
from incidentclue.lifecycle import runtime_fingerprint
from incidentclue.llm import ConfigurationError
from incidentclue.persistence import (
    ExecutionFailed, IncompatibleRuntime, InvestigationBusy, InvestigationFailed,
    InvestigationNotFound, RecoveryRequired, StorageUnavailable,
)

SECRET = "private-key-do-not-print"


def payload():
    return {"incident": {"incident_id": "checkout-api", "service": "checkout",
                         "summary": "Synthetic startup failure", "evidence": []},
            "health": {"service": "checkout", "evidence": []}}


@pytest.fixture
def no_database():
    @contextmanager
    def forbidden(_):
        pytest.fail("Invalid input reached the database")
        yield
    return TestClient(create_app(database_factory=forbidden))


@pytest.mark.parametrize("bad", [
    {}, {"extra": SECRET}, {"model": " "}, {"model": "m" * 201},
    {"incident": {"incident_id": "i", "service": "s", "summary": "x" * 13_000, "evidence": []}},
    {"health": {"service": "s", "observations": [SECRET]}},
    {"logs": {"service": "s", "evidence": [{"id": "x", "timestamp": "2026-01-01T00:00:00Z",
                                             "kind": "health", "content": SECRET}]}},
])
def test_invalid_creation_never_opens_storage(no_database, bad):
    body = {} if not bad else {**payload(), **bad}
    response = no_database.post("/investigations", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert SECRET not in response.text


@pytest.mark.parametrize("path", ["/investigations/not-a-uuid", "/investigations/not-a-uuid/pause",
                                 "/investigations/not-a-uuid/resume"])
def test_invalid_uuid_never_opens_storage(no_database, path):
    response = no_database.get(path) if path.count("/") == 2 else no_database.post(path)
    assert response.status_code == 422


def test_invalid_boundary_and_json(no_database):
    assert no_database.post("/investigations/00000000-0000-0000-0000-000000000000/resume?pause_after=bad").status_code == 422
    response = no_database.post("/investigations", content='{"secret":"' + SECRET)
    assert response.status_code == 422 and SECRET not in response.text


def test_body_limit_without_database(no_database):
    response = no_database.post("/investigations", content=b"x" * (MAX_BODY_BYTES + 1))
    assert response.status_code == 413


def test_chunked_body_limit_without_content_length(no_database):
    app = no_database.app
    chunks = iter([{"type": "http.request", "body": b"x" * 60_000, "more_body": True},
                   {"type": "http.request", "body": b"x" * 60_000, "more_body": False}])
    sent = []

    async def run():
        async def receive():
            return next(chunks)
        async def send(message):
            sent.append(message)
        await app({"type": "http", "asgi": {"version": "3.0"}, "method": "POST",
                   "scheme": "http", "path": "/investigations", "raw_path": b"/investigations",
                   "query_string": b"", "headers": [(b"transfer-encoding", b"chunked")],
                   "server": ("test", 80), "client": ("test", 100)}, receive, send)

    asyncio.run(run())
    assert sent[0]["status"] == 413


@pytest.mark.parametrize("error,status,code", [
    (StorageUnavailable(SECRET), 503, "storage_unavailable"),
    (ConfigurationError(SECRET), 503, "configuration_unavailable"),
    (InvestigationNotFound(SECRET), 404, "investigation_not_found"),
    (InvestigationBusy(SECRET), 409, "investigation_busy"),
    (IncompatibleRuntime(SECRET), 409, "incompatible_runtime"),
    (InvestigationFailed(SECRET, "provider_error"), 409, "investigation_failed"),
    (RecoveryRequired(SECRET), 409, "recovery_required"),
    (ExecutionFailed(SECRET, "provider_error"), 502, "provider_error"),
    (ExecutionFailed(SECRET, "validation_error"), 502, "execution_validation_error"),
    (ExecutionFailed(SECRET, "execution_error"), 500, "execution_error"),
    (RuntimeError(SECRET), 500, "internal_error"),
])
def test_controlled_errors_are_typed_and_sanitized(error, status, code, caplog):
    @contextmanager
    def broken(_):
        raise error
        yield
    client = TestClient(create_app(database_factory=broken))
    identifier = "00000000-0000-0000-0000-000000000000"
    response = client.post(f"/investigations/{identifier}/resume")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["investigation_id"] == identifier
    assert SECRET not in response.text + caplog.text


def test_missing_server_configuration_and_no_cors():
    client = TestClient(create_app(Settings()))
    response = client.get("/health", headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 503
    assert "access-control-allow-origin" not in response.headers


def test_openapi_and_adapter_import_do_not_change_fingerprint():
    before = runtime_fingerprint()
    app = create_app()
    spec = TestClient(app).get("/openapi.json").json()
    assert len(spec["paths"]) == 5
    for path, operations in spec["paths"].items():
        for operation in operations.values():
            assert "503" in operation["responses"]
    assert runtime_fingerprint() == before
    assert "private" not in repr(Settings(database_url=SECRET, provider_key=SECRET))


def test_guard_sanitizes_uncaught_route_errors(caplog):
    app = create_app()

    @app.get("/broken")
    def broken():
        raise RuntimeError(SECRET)

    response = TestClient(app).get("/broken")
    assert response.status_code == 500
    assert SECRET not in response.text + caplog.text


def test_payload_nulls_and_model_preservation():
    parsed = CreateInvestigation.model_validate({**payload(), "logs": None, "model": "saved-model"})
    assert parsed.model == "saved-model" and parsed.logs is None
    assert json.loads(parsed.model_dump_json())["health"] == {"service": "checkout", "evidence": []}
