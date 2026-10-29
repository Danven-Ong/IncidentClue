"""Durable CLI contracts, safe errors and persisted credential exclusion."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from incidentclue import cli
from incidentclue.lifecycle import Lifecycle
from incidentclue.persistence import open_database
from test_investigation import fake_client
from test_lifecycle import responses


def test_missing_database_configuration_is_controlled(monkeypatch, capsys):
    monkeypatch.delenv("INCIDENTCLUE_DATABASE_URL", raising=False)
    assert cli.main(["db-init"]) == 2
    assert "Set INCIDENTCLUE_DATABASE_URL" in capsys.readouterr().err


@pytest.mark.postgres
def test_cli_start_pause_inspect_resume_without_persisting_credentials(db, database_url, tmp_path, monkeypatch, capsys, incident_data):
    monkeypatch.setenv("INCIDENTCLUE_DATABASE_URL", database_url)
    monkeypatch.setenv("GROQ_API_KEY", "private-provider-key-for-this-test")
    monkeypatch.setattr(cli, "create_groq_client", lambda _: pytest.fail("provider created for read-only command"))
    assert cli.main(["db-init"]) == 0
    capsys.readouterr()
    incident_path = tmp_path / "incident.json"
    incident_data["evidence"] = []
    incident_path.write_text(json.dumps(incident_data), encoding="utf-8")
    health = Path(__file__).resolve().parents[2] / "data/synthetic/checkout-health.json"
    assert cli.main(["start", str(incident_path), "--health-file", str(health), "--paused"]) == 0
    printed = capsys.readouterr()
    view = json.loads(printed.out)
    identifier = view["investigation_id"]
    assert identifier in printed.err and view["status"] == "paused"
    assert cli.main(["inspect", identifier]) == 0
    assert json.loads(capsys.readouterr().out)["charged"] == {"model": 0, "tool": 0}
    assert cli.main(["pause", identifier]) == 0
    capsys.readouterr()
    requests = []
    monkeypatch.setattr(cli, "create_groq_client", lambda _: fake_client(responses(), requests))
    assert cli.main(["resume", identifier]) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["status"] == "completed" and len(requests) == 3
    monkeypatch.setattr(cli, "create_groq_client", lambda _: pytest.fail("completed ID executed"))
    monkeypatch.delenv("GROQ_API_KEY")
    assert cli.main(["resume", identifier]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == completed["state"]
    persisted = {
        "inputs": db.get(identifier)["inputs"], "runtime": db.get(identifier)["runtime"],
        "checkpoint": db.saver.get_tuple({"configurable": {"thread_id": identifier}}).checkpoint["channel_values"],
        "attempts": db.connection.execute("SELECT output FROM ic_attempts WHERE investigation_id=%s", (identifier,)).fetchall(),
    }
    from incidentclue.persistence import json_data
    text = json.dumps(json_data(persisted))
    assert "private-provider-key-for-this-test" not in text
    assert database_url not in text
    assert "test-key" not in text


@pytest.mark.postgres
def test_cli_busy_missing_failed_and_invalid_ids(db, database_url, monkeypatch, capsys):
    monkeypatch.setenv("INCIDENTCLUE_DATABASE_URL", database_url)
    monkeypatch.setattr(cli, "create_groq_client", lambda _: pytest.fail("unexpected execution"))
    assert cli.main(["inspect", "invalid"]) == 1
    assert "UUID" in capsys.readouterr().err
    assert cli.main(["resume", str(uuid4())]) == 1
    assert "not found" in capsys.readouterr().err
    identifier = str(uuid4())
    db.create(identifier, {}, {})
    with db.execution(identifier):
        assert cli.main(["resume", identifier]) == 4
        assert "another process" in capsys.readouterr().err


@pytest.mark.postgres
def test_database_error_diagnostics_do_not_echo_credentials(database_url, monkeypatch, capsys):
    # Malformed DSN exercises driver parsing without depending on external hosts.
    monkeypatch.setenv("INCIDENTCLUE_DATABASE_URL", "not-a-dsn private-db-password")
    assert cli.main(["db-init"]) == 1
    printed = capsys.readouterr()
    assert "private-db-password" not in printed.err and "Traceback" not in printed.err


@pytest.mark.postgres
def test_loss_of_ownership_connection_stops_after_first_charged_request(db, database_url, incident_data):
    import httpx2
    from openai import OpenAI
    from incidentclue.schemas import Incident
    from incidentclue.tools import load_health_snapshot
    from incidentclue.persistence import PersistenceError
    incident_data["evidence"] = []
    health = load_health_snapshot(Path(__file__).resolve().parents[2] / "data/synthetic/checkout-health.json")
    identifier = None
    requests = []
    with pytest.raises(PersistenceError, match="PostgreSQL operation failed"):
        with open_database(database_url) as owner:
            service = Lifecycle(owner)
            identifier = service.start(Incident.model_validate(incident_data), health)
            backend = owner.connection.info.backend_pid
            def handler(request):
                requests.append(request)
                db.connection.execute("SELECT pg_terminate_backend(%s)", (backend,))
                return httpx2.Response(200, json=responses()[0])
            service.resume(identifier, lambda: OpenAI(api_key="fake",
                http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    assert len(requests) == 1
    view = Lifecycle(db).inspect(identifier)
    assert view["recovery_needed"] and view["charged"]["model"] == 1
