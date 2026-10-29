"""Storage contracts against an explicitly selected, isolated PostgreSQL DB."""

import os
from uuid import uuid4

import pytest

from incidentclue.llm import ConfigurationError
from incidentclue.persistence import (
    InvestigationBusy, PersistenceError, RecoveryRequired,
    investigation_id, open_database,
)


@pytest.fixture
def database_url():
    url = os.environ.get("INCIDENTCLUE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set INCIDENTCLUE_TEST_DATABASE_URL to an isolated PostgreSQL database.")
    return url


@pytest.fixture
def db(database_url):
    with open_database(database_url) as database:
        database.initialize()
        yield database


def test_configuration_and_identity_are_controlled():
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        with open_database(""):
            pytest.fail("connected")
    with pytest.raises(PersistenceError, match="UUID"):
        investigation_id("not-an-id")
    assert investigation_id(str(uuid4()).upper())


@pytest.mark.postgres
def test_init_repeatable_and_missing_id(db):
    db.initialize()
    db.require_initialized()
    with pytest.raises(PersistenceError, match="not found"):
        db.get(str(uuid4()))


@pytest.mark.postgres
def test_execution_excludes_another_connection_but_pause_remains_available(db, database_url):
    identifier = str(uuid4())
    db.create(identifier, {}, {})
    with db.execution(identifier):
        db.begin(identifier)
        with open_database(database_url) as other:
            assert other.is_executing(identifier)
            with pytest.raises(InvestigationBusy):
                with other.execution(identifier):
                    pytest.fail("concurrent executor")
            assert other.request_pause(identifier)["pause_requested"]
    with open_database(database_url) as other:
        assert not other.is_executing(identifier)


@pytest.mark.postgres
def test_committed_output_reused_and_uncertain_attempt_stays_charged(db):
    identifier = str(uuid4())
    db.create(identifier, {}, {})
    state = {"model_requests_used": 0, "tool_calls_used": 0}
    update = {"model_requests_used": 1, "pending_call": None}
    with db.execution(identifier):
        db.begin(identifier)
        assert db.run_node(identifier, "decide", state, lambda _: update) == update
        assert db.run_node(identifier, "decide", state, lambda _: pytest.fail("replayed")) == update
        def die(_):
            raise KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            db.run_node(identifier, "diagnose", {**state, "model_requests_used": 1}, die)
        with pytest.raises(RecoveryRequired):
            db.run_node(identifier, "diagnose", {**state, "model_requests_used": 1},
                        lambda _: pytest.fail("uncertain replay"))
    assert [item["status"] for item in db.attempts(identifier)] == ["done", "in_flight"]
