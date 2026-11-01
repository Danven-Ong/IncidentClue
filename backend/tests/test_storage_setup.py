"""Web startup storage preparation: no CLI import or provider configuration."""

from contextlib import contextmanager

import pytest

from incidentclue.api import storage_setup
from incidentclue.llm import ConfigurationError
from incidentclue.persistence import StorageUnavailable


def test_setup_has_controlled_exit_without_database_configuration(monkeypatch, capsys):
    monkeypatch.delenv("INCIDENTCLUE_DATABASE_URL", raising=False)
    assert storage_setup.main() == 2
    assert "INCIDENTCLUE_DATABASE_URL" in capsys.readouterr().err


@pytest.mark.parametrize("error", [ConfigurationError, StorageUnavailable, RuntimeError])
def test_setup_never_prints_raw_failure_details(monkeypatch, capsys, error):
    @contextmanager
    def unavailable(url):
        raise error("configured-secret-diagnostic")
        yield
    monkeypatch.setattr(storage_setup, "open_database", unavailable)
    assert storage_setup.main() != 0
    output = capsys.readouterr()
    assert "configured-secret-diagnostic" not in output.err + output.out


@pytest.mark.postgres
def test_setup_preserves_saved_investigation_on_repeat(db):
    from uuid import uuid4
    identifier = str(uuid4())
    db.create(identifier, {"sentinel": "saved"}, {"sentinel": "runtime"})
    before = db.get(identifier)
    storage_setup.prepare_storage(db)
    storage_setup.prepare_storage(db)
    assert db.get(identifier) == before


@pytest.mark.postgres
def test_setup_refuses_application_schema_change_without_writes(db):
    before = db.connection.execute("SELECT version FROM ic_schema").fetchall()
    try:
        db.connection.execute("UPDATE ic_schema SET version=999")
        with pytest.raises(StorageUnavailable):
            storage_setup.prepare_storage(db)
        assert db.connection.execute("SELECT version FROM ic_schema").fetchall() == [{"version": 999}]
    finally:
        db.connection.execute("UPDATE ic_schema SET version=%s", (before[0]["version"],))


@pytest.mark.postgres
def test_setup_refuses_checkpoint_migration_instead_of_upgrading(db):
    versions = db.connection.execute("SELECT v FROM checkpoint_migrations ORDER BY v").fetchall()
    try:
        db.connection.execute("DELETE FROM checkpoint_migrations WHERE v=%s", (versions[-1]["v"],))
        before = db.connection.execute("SELECT v FROM checkpoint_migrations ORDER BY v").fetchall()
        with pytest.raises(StorageUnavailable):
            storage_setup.prepare_storage(db)
        assert db.connection.execute("SELECT v FROM checkpoint_migrations ORDER BY v").fetchall() == before
    finally:
        db.connection.execute("INSERT INTO checkpoint_migrations(v) VALUES (%s) ON CONFLICT DO NOTHING", (versions[-1]["v"],))
