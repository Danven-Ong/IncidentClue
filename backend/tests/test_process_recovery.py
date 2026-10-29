"""Separate-process checkpoints and actual os._exit crash windows."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from incidentclue.lifecycle import Lifecycle
from incidentclue.persistence import InvestigationBusy, RecoveryRequired
from test_lifecycle import responses
from test_investigation import fake_client

pytestmark = pytest.mark.postgres
WORKER = Path(__file__).with_name("durable_worker.py")


@pytest.fixture
def worker(database_url, tmp_path, incident_data):
    incident_data["evidence"] = []
    incident_path = tmp_path / "incident.json"
    incident_path.write_text(json.dumps(incident_data), encoding="utf-8")
    health_path = tmp_path / "health.json"
    root = Path(__file__).resolve().parents[2]
    health_path.write_bytes((root / "data/synthetic/checkout-health.json").read_bytes())
    requests = tmp_path / "requests.txt"
    environment = {
        **os.environ, "INCIDENTCLUE_TEST_DATABASE_URL": database_url,
        "WORKER_REQUESTS": str(requests), "GROQ_API_KEY": "", "OPENAI_API_KEY": "",
        "GROQ_MODEL": "changed-ambient-model",
        "WORKER_ENTERED": str(tmp_path / "entered"),
        "WORKER_RELEASE": str(tmp_path / "release"),
    }
    def run(command, identifier=None, *, crash="", boundary="", async_=False):
        arguments = [sys.executable, str(WORKER), command]
        arguments += [str(incident_path), str(health_path)] if command == "start" else [identifier]
        options = dict(env={**environment, "WORKER_CRASH": crash, "WORKER_PAUSE_AFTER": boundary},
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if async_:
            return subprocess.Popen(arguments, **options)
        return subprocess.run(arguments, timeout=30, **options)
    return run, environment, requests, incident_path, health_path


def output(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_separate_processes_restore_snapshots_model_and_history(db, worker):
    run, _, requests, incident_path, health_path = worker
    paused = output(run("start", boundary="execute"))
    identifier = paused["investigation_id"]
    assert paused["charged"] == {"model": 1, "tool": 1}
    incident_path.unlink()
    health_path.unlink()
    inspected = output(run("inspect", identifier))
    assert inspected["state"]["tool_history"] == paused["state"]["tool_history"]
    done = output(run("resume", identifier))
    assert done["status"] == "completed" and done["model"] == "saved-model"
    assert done["state"]["evidence"] == paused["state"]["evidence"]
    assert done["state"]["tool_history"] == paused["state"]["tool_history"]
    assert done["charged"] == {"model": 3, "tool": 1}
    assert len(requests.read_text().splitlines()) == 3
    assert output(run("resume", identifier))["state"] == done["state"]
    assert len(requests.read_text().splitlines()) == 3


@pytest.mark.parametrize("crash,exit_code,charged", [
    ("before", 72, 0), ("after_output", 73, 1), ("after_final", 74, 3),
])
def test_crashes_outside_uncertain_window_recover_without_extra_requests(db, worker, crash, exit_code, charged):
    run, _, requests, _, _ = worker
    first = output(run("start", boundary="decide"))
    identifier = first["investigation_id"]
    # The initial selection is committed. Crash injection now targets execute or
    # the later nodes, proving fetched output also survives checkpoint replay.
    result = run("resume", identifier, crash=crash)
    assert result.returncode == exit_code, result.stderr
    stale = output(run("inspect", identifier))
    assert stale["recovery_needed"] and not stale["executing"]
    # The first selection remains charged across every crash window.
    assert stale["charged"]["model"] >= max(1, charged)
    done = output(run("resume", identifier))
    assert done["status"] == "completed"
    assert done["charged"] == {"model": 3, "tool": 1}
    assert len(done["state"]["tool_history"]) == 1
    assert len(requests.read_text().splitlines()) == 3


def test_death_during_model_request_refuses_ambiguous_replay(db, worker):
    run, _, requests, _, _ = worker
    # Metadata-only recovery creates the first checkpoint after os._exit.
    metadata_only = run("start", crash="before_initial")
    assert metadata_only.returncode == 75
    identifier = json.loads(metadata_only.stdout)["investigation_id"]
    assert run("resume", identifier, crash="during").returncode == 71
    service = Lifecycle(db)
    with pytest.raises(RecoveryRequired, match="uncertain"):
        service.resume(identifier, lambda: pytest.fail("reissued uncertain request"))
    view = service.inspect(identifier)
    assert view["status"] == "recovery_required" and view["charged"]["model"] == 1
    assert len(requests.read_text().splitlines()) == 1


def test_metadata_only_creation_can_recover(db, worker):
    run, _, requests, _, _ = worker
    result = run("start", crash="before_initial")
    assert result.returncode == 75
    identifier = json.loads(result.stdout)["investigation_id"]
    done = output(run("resume", identifier))
    assert done["status"] == "completed" and len(requests.read_text().splitlines()) == 3


def test_concurrent_resume_rejected_and_external_pause_honored(db, worker):
    run, environment, requests, _, _ = worker
    first = output(run("start", boundary="execute"))
    identifier = first["investigation_id"]
    process = run("resume", identifier, crash="hold", async_=True)
    try:
        deadline = time.monotonic() + 15
        while not Path(environment["WORKER_ENTERED"]).exists():
            if process.poll() is not None:
                pytest.fail(process.communicate()[1])
            assert time.monotonic() < deadline, "Worker did not enter the request."
            time.sleep(0.05)
        service = Lifecycle(db)
        assert service.inspect(identifier)["executing"]
        with pytest.raises(InvestigationBusy):
            service.resume(identifier, lambda: pytest.fail("concurrent request"))
        assert service.pause(identifier)["pause_requested"]
        Path(environment["WORKER_RELEASE"]).touch()
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr
        paused = json.loads(stdout)
        assert paused["status"] == "paused" and paused["next_nodes"] == ["diagnose"]
        assert len(requests.read_text().splitlines()) == 2
        service.resume(identifier, lambda: fake_client(responses()[2:], []))
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
