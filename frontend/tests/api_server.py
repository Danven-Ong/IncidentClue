"""Browser-only FastAPI fixture: isolated PostgreSQL and fake SDK transport.

Never start this test server with production data. No provider key or live calls.
The application API and graph remain unchanged.
"""
import os
from pathlib import Path
import sys
import time

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/tests"))
from durable_worker import provider  # noqa: E402
from incidentclue.api.app import Settings, create_app  # noqa: E402
from incidentclue.persistence import open_database  # noqa: E402

url = os.environ["INCIDENTCLUE_TEST_DATABASE_URL"]
with open_database(url) as db:
    db.initialize()


def delayed_provider():
    client = provider()
    original = client.chat.completions.create

    def create(*args, **kwargs):
        time.sleep(2.5)
        return original(*args, **kwargs)

    client.chat.completions.create = create
    # Graph calls with_options; preserve our wrapped test method on copies.
    client.with_options = lambda **kwargs: client
    return client


os.environ["WORKER_REQUESTS"] = os.environ.get("WORKER_REQUESTS", str(Path(__file__).parent / "../test-results/provider-calls.txt"))
Path(os.environ["WORKER_REQUESTS"]).parent.mkdir(parents=True, exist_ok=True)
app = create_app(Settings(database_url=url), client_factory=delayed_provider)
uvicorn.run(app, host="127.0.0.1", port=8002, access_log=False, log_level="warning")
