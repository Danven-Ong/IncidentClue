"""Shared configuration and fixtures for application tests."""

import json
from pathlib import Path
from typing import Any

import pytest


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
