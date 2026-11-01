"""Detect uncertainty failures without mistaking fake runs or valid IDs for quality."""
import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def reviewer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "evals/rag"))
    module = importlib.import_module("review")
    monkeypatch.setattr(module.suite, "create_groq_client", lambda *a, **k: pytest.fail("Live client created"))
    return module


def diagnosis(status, ids=None, missing=None):
    return {"status": status, "probable_cause": "Unverified mechanism" if status == "hypothesis" else None,
            "evidence_ids": ["O1"] if ids is None else ids,
            "missing_evidence": ["Request-correlated observations"] if missing is None else missing,
            "next_checks": ["Inspect request-correlated observations"]}


def report(reviewer, tmp_path, results, remaining=None):
    rows = [{"type": "configuration", "mode": "fake_smoke", "split": "development",
             "fixture_sha256": reviewer.suite.validate()["fixture_sha256"],
             "case_count": len(results) + len(remaining or [])}]
    for ident, status, output, events in results:
        row = {"id": ident, "input_sha256": reviewer.suite.digest(ROOT / f"evals/rag/inputs/{ident}.json"),
               "status": status, "events": events}
        if output is not None:
            row["diagnosis"] = output
        rows.append(row)
    if remaining is not None:
        rows.append({"type": "stopped", "reason": "provider_rate_limit", "remaining_ids": remaining})
    path = tmp_path / "report.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return path


def test_valid_ids_do_not_hide_a_required_abstention_failure(reviewer, tmp_path):
    path = report(reviewer, tmp_path, [("case-08", "ok", diagnosis("hypothesis"), [])])
    result = reviewer.review(path)
    assert result["outcomes"][0]["reference_membership_ok"] is True
    assert result["required_abstentions_failed"] == 1
    assert result["automated_checks_passed"] is False
    assert result["quality_pass"] is False


def test_automatic_pass_still_requires_manual_semantic_review(reviewer, tmp_path):
    path = report(reviewer, tmp_path, [
        ("case-02", "ok", diagnosis("hypothesis"), []),
        ("case-08", "ok", diagnosis("insufficient_evidence"), []),
    ])
    result = reviewer.review(path)
    assert result["automated_checks_passed"] is True
    assert result["required_abstentions_passed"] == 1
    assert result["quality_pass"] is None
    assert result["semantic_support"] == "manual_review_required"
    assert result["mode"] == "fake_smoke"


def test_abstaining_on_every_case_is_not_a_quality_success(reviewer, tmp_path):
    path = report(reviewer, tmp_path, [("case-02", "ok", diagnosis("insufficient_evidence"), [])])
    result = reviewer.review(path)
    assert result["answerable_cases_abstained"] == 1
    assert result["automated_checks_passed"] is False


def test_rate_limit_is_unassessed_not_an_abstention_pass_or_failure(reviewer, tmp_path):
    path = report(reviewer, tmp_path, [
        ("case-08", "ok", diagnosis("hypothesis"), []),
        ("case-12", "error", None, []),
    ], remaining=["case-07"])
    result = reviewer.review(path)
    assert result["required_abstentions_assessed"] == 1
    assert result["required_abstentions_failed"] == 1
    assert result["required_abstentions_unassessed"] == 2
    assert result["required_abstentions"] == 3
    assert result["unattempted_ids"] == ["case-07"]
    assert result["execution_errors"] == 1
    assert result["planned_cases"] == 3


@pytest.mark.parametrize("status,valid", [("ok", True), ("error", False)])
def test_only_successfully_returned_observations_are_citable(reviewer, tmp_path, status, valid):
    event = {"result": {"status": status, "evidence": [{"id": "T1", "timestamp": "2026-09-15T10:00:00Z",
        "kind": "health", "content": "Readiness is false"}]}}
    path = report(reviewer, tmp_path, [("case-08", "ok", diagnosis("insufficient_evidence", ids=["T1"]), [event])])
    result = reviewer.review(path)
    assert result["outcomes"][0]["reference_membership_ok"] is valid


def test_abstention_with_only_blank_missing_evidence_is_flagged(reviewer, tmp_path):
    path = report(reviewer, tmp_path, [("case-08", "ok", diagnosis("insufficient_evidence", missing=[" "]), [])])
    result = reviewer.review(path)
    assert result["required_abstentions_passed"] == 1
    assert result["outcomes"][0]["missing_evidence_present"] is False
    assert result["automated_checks_passed"] is False


@pytest.mark.parametrize("change", ["input_hash", "fixture_hash", "split", "duplicate"])
def test_mismatched_or_combined_reports_are_rejected(reviewer, tmp_path, change):
    path = report(reviewer, tmp_path, [("case-08", "ok", diagnosis("hypothesis"), [])])
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if change == "input_hash":
        rows[1]["input_sha256"] = "wrong"
    elif change == "fixture_hash":
        rows[0]["fixture_sha256"] = "wrong"
    elif change == "split":
        rows[0]["split"] = "held_out"
    else:
        rows.append(rows[1])
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    with pytest.raises(ValueError):
        reviewer.review(path)


def test_empty_evaluation_cannot_pass_checks(reviewer, tmp_path):
    path = report(reviewer, tmp_path, [])
    with pytest.raises(ValueError, match="Planned case count"):
        reviewer.review(path)
