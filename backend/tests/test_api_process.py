"""Real HTTP/server process deaths, lost responses and safe checkpoint recovery."""

from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest

from incidentclue.persistence import lock_key
from test_api import payload
from test_process_recovery import wait_for_disconnect

pytestmark = pytest.mark.postgres
WORKER = Path(__file__).with_name("api_worker.py")


@pytest.fixture
def server(db, database_url, tmp_path):
    requests = tmp_path / "requests.txt"
    entered, release = tmp_path / "entered", tmp_path / "release"

    @contextmanager
    def start(crash=""):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        environment = {**os.environ, "INCIDENTCLUE_TEST_DATABASE_URL": database_url,
                       "GROQ_API_KEY": "", "OPENAI_API_KEY": "", "WORKER_REQUESTS": str(requests),
                       "WORKER_CRASH": crash, "WORKER_ENTERED": str(entered), "WORKER_RELEASE": str(release)}
        with (tmp_path / f"server-{port}.log").open("w+") as logs:
            process = subprocess.Popen([sys.executable, str(WORKER), str(port)], env=environment,
                                       stdout=logs, stderr=logs,
                                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False) as client:
                try:
                    deadline = time.monotonic() + 15
                    while True:
                        assert process.poll() is None, "Test API server exited before readiness"
                        try:
                            if client.get("/health").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        assert time.monotonic() < deadline, "Test API server did not become ready"
                        time.sleep(0.05)
                    yield client, process
                finally:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    return start, requests, entered, release


def body():
    import json
    result = payload()
    result["health"] = json.loads((WORKER.parents[2] / "data/synthetic/checkout-health.json").read_text())
    return result


@pytest.mark.parametrize("crash,exit_code,expected_calls", [
    ("before", 72, 3), ("after_output", 73, 3), ("after_final", 74, 3), ("during", 71, 1),
])
def test_http_process_death_recovery_and_uncertain_refusal(db, server, crash, exit_code, expected_calls):
    start, requests, _, _ = server
    with start(crash) as (client, process):
        first = client.post("/investigations", json=body()).json()
        identifier = first["investigation_id"]
        url = f"/investigations/{identifier}"
        with pytest.raises(httpx.TransportError):
            client.post(url + "/resume")
        assert process.wait(timeout=10) == exit_code
    wait_for_disconnect(db, identifier)
    with start() as (client, _):
        observed = client.get(url).json()
        assert observed["investigation_id"] == identifier
        assert observed["recovery_needed"] and not observed["executing"]
        response = client.post(url + "/resume")
        final = client.get(url).json()
        if crash == "during":
            assert response.status_code == 409
            assert response.json()["error"]["cause"] == "uncertain_attempt"
            assert final["status"] == "recovery_required"
            assert final["charged"] == {"model": 1, "tool": 0}
        else:
            assert response.status_code == 200, response.text
            assert final["status"] == "completed" and final["charged"] == {"model": 3, "tool": 1}
            assert len(final["state"]["tool_history"]) == 1
            assert client.post(url + "/resume").json()["state"] == final["state"]
        assert len(requests.read_text().splitlines()) == expected_calls


def test_lost_resume_response_does_not_cancel_and_inspection_remains_available(db, server):
    start, requests, entered, release = server
    with start("hold") as (client, _):
        identifier = client.post("/investigations", json=body()).json()["investigation_id"]
        url = f"/investigations/{identifier}"
        try:
            with pytest.raises(httpx.ReadTimeout):
                client.post(url + "/resume", timeout=0.3)
            # A client timeout need not wait for database ownership and provider
            # construction. Synchronize on entry rather than machine speed.
            deadline = time.monotonic() + 10
            while not entered.exists():
                assert time.monotonic() < deadline, "Provider did not start after the lost response"
                time.sleep(0.05)
            assert client.get(url).json()["executing"]
            assert client.post(url + "/resume").status_code == 409
        finally:
            release.touch()
        deadline = time.monotonic() + 10
        while True:
            view = client.get(url).json()
            if view["status"] == "completed":
                break
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert view["charged"] == {"model": 3, "tool": 1}
        assert len(requests.read_text().splitlines()) == 3


def test_lost_database_ownership_stops_server_execution_and_refuses_replay(db, server):
    from concurrent.futures import ThreadPoolExecutor

    start, requests, entered, release = server
    with start("hold") as (client, _):
        identifier = client.post("/investigations", json=body()).json()["investigation_id"]
        url = f"/investigations/{identifier}"
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, url + "/resume")
            try:
                deadline = time.monotonic() + 10
                while not entered.exists():
                    assert time.monotonic() < deadline
                    time.sleep(0.05)
                key = lock_key(identifier)
                owner = db.connection.execute("""
                    SELECT pid FROM pg_locks WHERE locktype='advisory' AND granted
                      AND classid=%s::oid AND objid=%s::oid AND objsubid=1
                      AND database=(SELECT oid FROM pg_database WHERE datname=current_database())
                """, ((key >> 32) & 0xFFFFFFFF, key & 0xFFFFFFFF)).fetchone()["pid"]
                db.connection.execute("SELECT pg_terminate_backend(%s)", (owner,))
            finally:
                release.touch()
            response = pending.result(timeout=10)
            assert response.status_code == 503, response.text
        assert client.get("/health").status_code == 200
        refused = client.post(url + "/resume")
        assert refused.status_code == 409
        assert refused.json()["error"]["cause"] == "uncertain_attempt"
        assert len(requests.read_text().splitlines()) == 1
