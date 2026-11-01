"""Real Uvicorn subprocess fixture with fake provider and explicit crash windows."""

from contextlib import contextmanager
import os
import sys

import uvicorn

from incidentclue.api.app import Settings, create_app
from incidentclue.persistence import open_database
from durable_worker import provider


@contextmanager
def database(url):
    with open_database(url) as db:
        crash = os.environ.get("WORKER_CRASH", "")
        if crash in ("before", "after_output"):
            original_node = db.run_node

            def node(*args):
                if crash == "before":
                    os._exit(72)
                original_node(*args)
                os._exit(73)

            db.run_node = node
        if crash == "after_final":
            original_status = db.status

            def status(identifier, value, error_code=None):
                if value == "completed":
                    os._exit(74)
                return original_status(identifier, value, error_code)

            db.status = status
        yield db


if __name__ == "__main__":
    app = create_app(Settings(database_url=os.environ["INCIDENTCLUE_TEST_DATABASE_URL"]),
                     database_factory=database, client_factory=provider)
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning", access_log=False,
                timeout_graceful_shutdown=2)
