"""Shared configuration and fixtures for application tests."""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from incidentclue.persistence import open_database


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


@pytest.fixture
def incident_data() -> dict[str, Any]:
    example = Path(__file__).resolve().parents[2] / "data/synthetic/checkout.json"
    return json.loads(example.read_text(encoding="utf-8-sig"))


@pytest.fixture
def diagnosis_data() -> dict[str, Any]:
    return {
        "status": "hypothesis",
        "probable_cause": "Checkout cannot start because PAYMENT_API_URL is missing.",
        "evidence_ids": ["E2"],
        "missing_evidence": ["Effective deployment configuration."],
        "next_checks": ["Inspect environment variables in the deployment."],
    }
