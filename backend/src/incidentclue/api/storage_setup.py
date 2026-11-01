"""One-shot storage prerequisite for the local web services, independent of CLI."""

import os
import sys

from incidentclue.llm import ConfigurationError
from incidentclue.persistence import INIT_LOCK, StorageUnavailable, open_database


def prepare_storage(database):
    """Initialize fresh storage without upgrading an existing incompatible schema."""
    # Serialize the preflight too: another initializer may be midway through
    # creating fresh checkpoint tables. initialize() takes the same reentrant
    # session lock and releases its own acquisition before this one is released.
    database.connection.execute("SELECT pg_advisory_lock(%s)", (INIT_LOCK,))
    try:
        tables = database.connection.execute("""
            SELECT to_regclass('ic_schema') AS application,
                   to_regclass('checkpoint_migrations') AS checkpoints
        """).fetchone()
        if tables["application"] is not None:
            database.require_initialized()
        if tables["checkpoints"] is not None:
            version = database.connection.execute("SELECT max(v) AS version FROM checkpoint_migrations").fetchone()
            if version["version"] != len(database.saver.MIGRATIONS) - 1:
                raise StorageUnavailable("Checkpoint schema requires explicit administrative setup.")
        database.initialize()
    finally:
        database.connection.execute("SELECT pg_advisory_unlock(%s)", (INIT_LOCK,))


def main():
    try:
        with open_database(os.environ.get("INCIDENTCLUE_DATABASE_URL", "")) as database:
            prepare_storage(database)
    except ConfigurationError:
        print("Storage setup failed: configure INCIDENTCLUE_DATABASE_URL.", file=sys.stderr)
        return 2
    except StorageUnavailable:
        print("Storage setup failed: database unavailable or schema incompatible. Check storage before restarting; no automatic migration is performed.", file=sys.stderr)
        return 1
    except Exception:
        # Driver/runtime details may contain credentials. Never log raw errors.
        print("Storage setup failed unexpectedly. Check storage configuration.", file=sys.stderr)
        return 1
    print("Storage ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
