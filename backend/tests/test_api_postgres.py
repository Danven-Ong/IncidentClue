"""HTTP/CLI parity and concurrent operations against real PostgreSQL checkpoints."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
from threading import Event, get_ident
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
import pytest

from incidentclue import cli, lifecycle
from incidentclue.api.app import Settings, create_app
from incidentclue.persistence import open_database
from test_api import payload
from test_investigation import completion, fake_client
from test_lifecycle import responses

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def body():
    return {**payload(), "health": json.loads((ROOT / "data/synthetic/checkout-health.json").read_text()),
            "model": "saved-api-model"}


@pytest.fixture
def api(db, database_url):
    requests = []
    app = create_app(Settings(database_url=database_url, model="configured-model"),
                     client_factory=lambda: fake_client(responses(), requests))
    with TestClient(app) as client:
        yield client, requests


def create(client, body):
    response = client.post("/investigations", json=body)
    assert response.status_code == 201, response.text
    result = response.json()
    assert response.headers["Location"] == f"/investigations/{result['investigation_id']}"
    return result


def test_create_pause_resume_completed_and_saved_settings(api, body, monkeypatch):
    client, requests = api
    first = create(client, body)
    identifier = first["investigation_id"]
    url = f"/investigations/{identifier}"
    assert first["status"] == "paused" and first["charged"] == {"model": 0, "tool": 0}
    assert requests == [] and first["attempts"] == []
    assert client.get("/health").json() == {"status": "ok", "database": "ready"}
    paused = client.post(url + "/resume?pause_after=execute").json()
    assert paused["status"] == "paused" and paused["charged"] == {"model": 1, "tool": 1}
    assert client.post(url + "/pause").json()["pause_requested"]
    done = client.post(url + "/resume").json()
    assert done["status"] == "completed" and done["charged"] == {"model": 3, "tool": 1}
    assert done["state"]["evidence"] == paused["state"]["evidence"]
    assert done["state"]["tool_history"] == paused["state"]["tool_history"]
    assert all(request["model"] == "saved-api-model" for request in requests)
    # A fresh app with no key/fake factory still reads and re-resumes completed work.
    with TestClient(create_app(Settings(database_url=client.app.state.settings.database_url))) as fresh:
        assert fresh.get(url).json() == done
        assert fresh.post(url + "/resume").json() == done
        terminal_pause = fresh.post(url + "/pause").json()
        assert terminal_pause == done and not terminal_pause["pause_requested"]
    assert len(requests) == 3


@pytest.mark.parametrize("boundary", ["decide", "execute", "diagnose", "validate"])
def test_each_http_pause_boundary(api, body, boundary):
    client, requests = api
    identifier = create(client, body)["investigation_id"]
    response = client.post(f"/investigations/{identifier}/resume?pause_after={boundary}")
    assert response.status_code == 200
    assert response.json()["status"] == ("completed" if boundary == "validate" else "paused")
    assert client.post(f"/investigations/{identifier}/resume").json()["status"] == "completed"
    assert len(requests) == 3


def test_actual_cli_interoperability_and_deterministic_parity(api, body, database_url, monkeypatch, capsys, tmp_path):
    client, requests = api
    monkeypatch.setenv("INCIDENTCLUE_DATABASE_URL", database_url)
    monkeypatch.setenv("GROQ_MODEL", "saved-api-model")
    first = create(client, body)
    identifier = first["investigation_id"]
    assert cli.main(["inspect", identifier]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == first["state"]
    cli_requests = []
    monkeypatch.setattr(cli, "create_groq_client", lambda _: fake_client(responses(), cli_requests))
    assert cli.main(["resume", identifier]) == 0
    from_cli = json.loads(capsys.readouterr().out)
    assert client.get(f"/investigations/{identifier}").json()["state"] == from_cli["state"]

    incident_path = tmp_path / "incident.json"
    incident_path.write_text(json.dumps(body["incident"]))
    assert cli.main(["start", str(incident_path), "--health-file",
                     str(ROOT / "data/synthetic/checkout-health.json"), "--paused"]) == 0
    second = json.loads(capsys.readouterr().out)
    from_api = client.post(f"/investigations/{second['investigation_id']}/resume").json()
    assert from_api["state"] == from_cli["state"]
    assert from_api["charged"] == from_cli["charged"] == {"model": 3, "tool": 1}
    assert len(requests) == len(cli_requests) == 3


def test_missing_key_does_not_charge_or_destroy_resumable_work(db, database_url, body):
    with TestClient(create_app(Settings(database_url=database_url))) as client:
        identifier = create(client, body)["investigation_id"]
        response = client.post(f"/investigations/{identifier}/resume")
        assert response.status_code == 503
        view = client.get(f"/investigations/{identifier}").json()
        assert view["status"] == "paused" and view["charged"] == {"model": 0, "tool": 0}


@pytest.mark.parametrize("failure,code", [([429], "provider_error"),
    (responses()[:2] + [completion({"content": "not json"})], "execution_validation_error")])
def test_execution_failure_preserves_progress(db, database_url, body, failure, code):
    requests = []
    with TestClient(create_app(Settings(database_url=database_url),
                              client_factory=lambda: fake_client(failure, requests))) as client:
        identifier = create(client, body)["investigation_id"]
        url = f"/investigations/{identifier}"
        response = client.post(url + "/resume")
        assert response.status_code == 502, response.text
        assert response.json()["error"]["code"] == code
        assert response.json()["investigation_id"] == identifier
        view = client.get(url).json()
        assert view["status"] == "failed" and view["charged"]["model"] >= 1
        assert client.post(url + "/resume").status_code == 409
        assert client.post(url + "/pause").json()["status"] == "failed"


def test_missing_and_incompatible_ids(api, body, monkeypatch):
    client, requests = api
    missing = f"/investigations/{uuid4()}"
    for suffix in ("", "/resume", "/pause"):
        response = client.get(missing) if not suffix else client.post(missing + suffix)
        assert response.status_code == 404
    identifier = create(client, body)["investigation_id"]
    monkeypatch.setattr(lifecycle, "runtime_fingerprint", lambda: {"changed": True})
    url = f"/investigations/{identifier}"
    assert client.get(url).json()["compatible"] is False
    assert client.post(url + "/resume").json()["error"]["code"] == "incompatible_runtime"
    assert client.get(url).json()["status"] == "paused"
    assert not requests


def test_uncertain_attempt_and_missing_checkpoint_refuse(api, body, db):
    client, requests = api
    identifier = create(client, body)["investigation_id"]
    db.connection.execute("INSERT INTO ic_attempts (investigation_id, operation, kind, status) VALUES (%s, %s, %s, %s)",
                          (identifier, "decide:0:0", "model", "in_flight"))
    url = f"/investigations/{identifier}"
    response = client.post(url + "/resume")
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "recovery_required", "cause": "uncertain_attempt",
        "message": "Recovery requires inspection; execution was refused.",
    }
    assert client.post(url + "/resume").json()["error"]["cause"] == "uncertain_attempt"
    assert client.get(url).json()["charged"]["model"] == 1
    assert not requests


def test_missing_checkpoint_with_committed_attempt_is_inspectable_and_refused(api, body, db):
    client, requests = api
    identifier = create(client, body)["investigation_id"]
    url = f"/investigations/{identifier}"
    assert client.post(url + "/resume?pause_after=decide").status_code == 200
    db.saver.delete_thread(identifier)
    observed = client.get(url).json()
    assert observed["state"] == {} and observed["charged"] == {"model": 1, "tool": 0}
    refused = client.post(url + "/resume")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "recovery_required"
    assert len(requests) == 1


def test_stored_runtime_mismatch_preserves_inputs_and_history(api, body, db):
    from psycopg.types.json import Jsonb

    client, requests = api
    identifier = create(client, body)["investigation_id"]
    before = db.get(identifier)
    old_runtime = {**before["runtime"], "source_sha256": "older-reviewed-source"}
    db.connection.execute("UPDATE ic_investigations SET runtime=%s WHERE id=%s", (Jsonb(old_runtime), identifier))
    url = f"/investigations/{identifier}"
    observed = client.get(url).json()
    assert not observed["compatible"] and observed["state"]
    assert client.post(url + "/resume").status_code == 409
    after = db.get(identifier)
    assert after["inputs"] == before["inputs"] and after["runtime"] == old_runtime
    assert after["status"] == "paused" and after["updated_at"] == before["updated_at"]
    assert not requests


def test_http_and_cli_contention_inspection_and_cooperative_pause(db, database_url, body, monkeypatch, capsys):
    import httpx2
    from openai import OpenAI
    entered, release = Event(), Event()
    requests = []

    def factory():
        def handler(request):
            requests.append(request)
            entered.set()
            assert release.wait(10), "Concurrent test did not release request"
            return httpx2.Response(200, json=responses()[0])
        return OpenAI(api_key="fake", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))

    with TestClient(create_app(Settings(database_url=database_url), client_factory=factory)) as client:
        identifier = create(client, body)["investigation_id"]
        url = f"/investigations/{identifier}"
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, url + "/resume")
            try:
                assert entered.wait(10)
                assert client.get(url).json()["executing"]
                competing = client.post(url + "/resume")
                assert competing.status_code == 409
                assert competing.json()["error"]["code"] == "investigation_busy"
                monkeypatch.setenv("INCIDENTCLUE_DATABASE_URL", database_url)
                assert cli.main(["resume", identifier]) == 4
                capsys.readouterr()
                observed = client.post(url + "/pause").json()
                assert observed["status"] == "running" and observed["pause_requested"]
            finally:
                release.set()
            stopped = pending.result(timeout=10).json()
            assert stopped["status"] == "paused" and stopped["charged"] == {"model": 1, "tool": 0}
        # CLI-owned execution excludes the API too.
        with db.execution(identifier):
            assert client.post(url + "/resume").status_code == 409
            assert client.get(url).status_code == 200
        assert len(requests) == 1


def test_resources_are_created_used_and_closed_in_same_worker_thread(db, database_url, body):
    threads = []

    @contextmanager
    def tracked(url):
        threads.append(("open", get_ident()))
        with open_database(url) as database:
            try:
                yield database
            finally:
                threads.append(("close", get_ident()))

    main_thread = get_ident()
    with TestClient(create_app(Settings(database_url=database_url), database_factory=tracked)) as client:
        create(client, body)
        client.get("/health")
    assert len(threads) == 4
    assert all(threads[i][1] == threads[i + 1][1] != main_thread for i in (0, 2))


def test_readiness_partial_schema_and_recovery_without_automatic_writes(db, database_url):
    schema = "api_test_" + uuid4().hex
    db.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    isolated_url = psycopg.conninfo.make_conninfo(database_url, options=f"-csearch_path={schema}")
    try:
        with TestClient(create_app(Settings(database_url=isolated_url))) as client:
            assert client.get("/health").status_code == 503
            tables = db.connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s", (schema,)).fetchall()
            assert not tables  # health did not initialize storage
            with open_database(isolated_url) as isolated:
                isolated.initialize()
                assert client.get("/health").status_code == 200
                isolated.connection.execute("UPDATE ic_schema SET version=999")
                assert client.get("/health").status_code == 503
                assert isolated.connection.execute("SELECT version FROM ic_schema").fetchone()["version"] == 999
                isolated.connection.execute("UPDATE ic_schema SET version=1")
                isolated.connection.execute("DROP TABLE checkpoint_writes")
                assert client.get("/health").status_code == 503
    finally:
        db.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_model_fallback_is_saved(api, body):
    client, _ = api
    del body["model"]
    assert create(client, body)["model"] == "configured-model"
