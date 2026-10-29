"""Audit regressions: non-interfering inspection and incompatible schema safety."""

from contextlib import contextmanager
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

from incidentclue.persistence import PersistenceError, lock_key, open_database

pytestmark = pytest.mark.postgres


@contextmanager
def isolated_schema(database_url):
    # Every destructive cleanup targets only this freshly created audit schema.
    schema = "ic_audit_" + uuid4().hex
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            yield make_conninfo(database_url, options=f"-c search_path={schema}")
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.parametrize("versions", [[2], [1, 2]])
def test_db_init_does_not_modify_an_incompatible_schema(database_url, versions):
    with isolated_schema(database_url) as url:
        with open_database(url) as db:
            db.connection.execute("CREATE TABLE ic_schema (version integer PRIMARY KEY)")
            for version in versions:
                db.connection.execute("INSERT INTO ic_schema VALUES (%s)", (version,))
            with pytest.raises(PersistenceError, match="incompatible"):
                db.initialize()
            actual = db.connection.execute("SELECT version FROM ic_schema ORDER BY version").fetchall()
            assert actual == [{"version": item} for item in versions]
            assert db.connection.execute("SELECT to_regclass('ic_investigations') AS table_name").fetchone()["table_name"] is None


@pytest.mark.parametrize("negative", [False, True])
def test_inspection_does_not_temporarily_block_execution(db, database_url, monkeypatch, negative):
    identifier = str(uuid4())
    while (lock_key(identifier) < 0) != negative:
        identifier = str(uuid4())
    db.create(identifier, {}, {})
    with open_database(database_url) as reader:
        execute = reader.connection.execute
        def read_while_an_executor_starts(*args, **kwargs):
            result = execute(*args, **kwargs)
            # Force an executor into the exact gap between the inspector's SQL
            # statement and its result handling. Inspection must not own the lock.
            with db.execution(identifier):
                pass
            return result
        monkeypatch.setattr(reader.connection, "execute", read_while_an_executor_starts)
        assert not reader.is_executing(identifier)


def test_owner_can_inspect_execution_without_losing_ownership(db, database_url):
    identifier = str(uuid4())
    db.create(identifier, {}, {})
    with db.execution(identifier):
        assert db.is_executing(identifier)
        with open_database(database_url) as observer:
            assert observer.is_executing(identifier)
    assert not db.is_executing(identifier)
