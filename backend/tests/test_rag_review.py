"""Detect uncertainty failures without mistaking fake runs or valid IDs for quality."""
import importlib
import json
from pathlib import Path
import shutil

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


def report(reviewer, tmp_path, results, remaining=None, split="development"):
    rows = [{"type": "configuration", "mode": "fake_smoke", "split": split,
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


def test_no_guidance_supported_answer_is_a_positive_decision_control(reviewer, tmp_path):
    path = report(reviewer, tmp_path, [("case-11", "ok", diagnosis("insufficient_evidence"), [])])
    result = reviewer.review(path)
    assert result["review_contract"] == "decision-v2"
    assert result["answerable_cases_abstained"] == 0  # Legacy category-specific meaning retained.
    assert result["unexpected_abstentions"] == 1
    assert result["unexpected_hypotheses"] == 0
    assert result["expected_status_counts"]["hypothesis"] == {
        "planned": 1, "assessed": 1, "matched": 0, "mismatched": 1, "unassessed": 0}
    assert result["outcomes"][0]["expected_status"] == "hypothesis"
    assert result["outcomes"][0]["expected_status_ok"] is False
    assert result["quality_pass"] is False


def test_legacy_review_keeps_prior_checks_without_reinterpreting_category(reviewer, tmp_path, monkeypatch):
    path = report(reviewer, tmp_path, [("case-11", "ok", diagnosis("insufficient_evidence"), [])])
    original_load = reviewer.suite.load
    def no_new_metadata(path):
        assert path.name not in (reviewer.ANNOTATION_FILE, "decision-contract.v2.json")
        return original_load(path)
    monkeypatch.setattr(reviewer.suite, "load", no_new_metadata)
    result = reviewer.review(path, contract="legacy-v1")
    assert result["review_contract"] == "legacy-v1"
    assert result["decision_annotations"] is None
    assert result["expected_status_counts"] is None
    assert result["unexpected_abstentions"] is result["unexpected_hypotheses"] is None
    assert result["answerable_cases_abstained"] == 0
    assert result["automated_checks_passed"] is True
    assert result["quality_pass"] is None


def test_no_guidance_insufficient_case_is_not_a_false_regression(reviewer, tmp_path):
    path = report(reviewer, tmp_path, [("case-12", "ok", diagnosis("insufficient_evidence"), [])])
    result = reviewer.review(path)
    assert result["unexpected_abstentions"] == result["unexpected_hypotheses"] == 0
    assert result["expected_status_counts"]["insufficient_evidence"] == {
        "planned": 1, "assessed": 1, "matched": 1, "mismatched": 0, "unassessed": 0}
    assert result["expected_status_counts"]["hypothesis"]["planned"] == 0
    assert result["automated_checks_passed"] is True
    assert result["quality_pass"] is None
    assert result["decision_annotations"]["status"] == "proposed_assistant_annotations_not_independent_ground_truth"


@pytest.mark.parametrize("status,abstentions,hypotheses", [
    ("insufficient_evidence", 7, 0), ("hypothesis", 0, 5),
])
def test_uniform_outputs_fail_across_all_twelve_decision_targets(
    reviewer, tmp_path, status, abstentions, hypotheses,
):
    results = [(f"case-{i:02}", "ok", diagnosis(status), []) for i in range(1, 13)]
    result = reviewer.review(report(reviewer, tmp_path, results))
    assert result["expected_status_counts"]["hypothesis"]["planned"] == 7
    assert result["expected_status_counts"]["insufficient_evidence"]["planned"] == 5
    assert result["unexpected_abstentions"] == abstentions
    assert result["unexpected_hypotheses"] == hypotheses
    assert result["quality_pass"] is False


def test_positive_execution_errors_and_unattempted_cases_remain_unassessed(reviewer, tmp_path):
    path = report(reviewer, tmp_path, [
        ("case-11", "error", None, []),
        ("case-12", "ok", diagnosis("insufficient_evidence"), []),
    ], remaining=["case-02"])
    result = reviewer.review(path)
    assert result["expected_status_counts"]["hypothesis"] == {
        "planned": 2, "assessed": 0, "matched": 0, "mismatched": 0, "unassessed": 2}
    assert result["expected_status_counts"]["insufficient_evidence"]["matched"] == 1
    assert result["unexpected_abstentions"] == 0
    assert result["outcomes"][0]["expected_status_ok"] is None
    assert result["quality_pass"] is False


@pytest.fixture
def contract_copy(reviewer, tmp_path, monkeypatch):
    copied = tmp_path / "evaluation"
    shutil.copytree(ROOT / "evals/rag", copied)
    monkeypatch.setattr(reviewer.suite, "SUITE", copied)
    return copied


def write_annotations(reviewer, directory, value, *, update_hash=True):
    path = directory / reviewer.ANNOTATION_FILE
    path.write_text(json.dumps(value), encoding="utf-8")
    if update_hash:
        manifest_path = directory / "decision-contract.v2.json"
        manifest = reviewer.suite.load(manifest_path)
        manifest["annotation_sha256"] = reviewer.suite.digest(path)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize("change", [
    "duplicate", "foreign_id", "missing_case", "split", "fixture", "version", "bool_version",
    "provenance", "runtime_flag", "unknown_status", "blank_scope", "conflicting_target", "extra_field",
])
def test_invalid_decision_annotations_are_rejected_even_with_updated_file_hash(
    reviewer, contract_copy, tmp_path, change,
):
    annotations = reviewer.suite.load(contract_copy / reviewer.ANNOTATION_FILE)
    if change == "duplicate":
        annotations["cases"].append(annotations["cases"][0])
    elif change == "foreign_id":
        annotations["cases"][0]["id"] = "case-13"
    elif change == "missing_case":
        annotations["cases"].pop()
    elif change == "split":
        annotations["split"] = "held_out"
    elif change == "fixture":
        annotations["fixture_sha256"] = "different-version"
    elif change == "version":
        annotations["annotation_version"] = 2
    elif change == "bool_version":
        annotations["annotation_version"] = True
    elif change == "provenance":
        annotations["status"] = "independently_validated"
    elif change == "runtime_flag":
        annotations["runtime_use_permitted"] = True
    elif change == "unknown_status":
        annotations["cases"][0]["expected_status"] = "maybe"
    elif change == "blank_scope":
        annotations["cases"][0]["supported_scope"] = " "
    elif change == "extra_field":
        annotations["cases"][0]["unexpected"] = "value"
    else:
        annotations["cases"][6] = {"id": "case-07", "expected_status": "hypothesis",
            "supported_scope": "Invalid target", "unsupported_extension": "Invalid target"}
    write_annotations(reviewer, contract_copy, annotations)
    path = report(reviewer, tmp_path, [("case-11", "ok", diagnosis("hypothesis"), [])])
    with pytest.raises(ValueError, match="Decision"):
        reviewer.review(path)


@pytest.mark.parametrize("change", ["stale_hash", "path_escape", "fixture", "split", "contract"])
def test_invalid_contract_manifest_or_unrecorded_annotations_refuse_review(
    reviewer, contract_copy, tmp_path, change,
):
    if change == "stale_hash":
        annotations = reviewer.suite.load(contract_copy / reviewer.ANNOTATION_FILE)
        annotations["cases"][0]["supported_scope"] = "Changed without version manifest update"
        write_annotations(reviewer, contract_copy, annotations, update_hash=False)
    else:
        path = contract_copy / "decision-contract.v2.json"
        manifest = reviewer.suite.load(path)
        key, value = {"path_escape": ("annotation_file", "../labels.json"),
                      "fixture": ("fixture_sha256", "other"), "split": ("split", "held_out"),
                      "contract": ("review_contract", "unknown")}[change]
        manifest[key] = value
        path.write_text(json.dumps(manifest), encoding="utf-8")
    path = report(reviewer, tmp_path, [("case-11", "ok", diagnosis("hypothesis"), [])])
    with pytest.raises(ValueError, match="Decision"):
        reviewer.review(path)


def test_new_annotation_scope_has_a_new_contract_identity_without_changing_raw_report(
    reviewer, contract_copy, tmp_path,
):
    path = report(reviewer, tmp_path, [("case-11", "ok", diagnosis("hypothesis"), [])])
    before = reviewer.review(path)
    annotations = reviewer.suite.load(contract_copy / reviewer.ANNOTATION_FILE)
    annotations["cases"][10]["supported_scope"] += " Reviewed scope clarification."
    write_annotations(reviewer, contract_copy, annotations)
    after = reviewer.review(path)
    assert before["review_contract_sha256"] != after["review_contract_sha256"]
    assert before["source_report_sha256"] == after["source_report_sha256"]
    assert before["fixture_sha256"] == after["fixture_sha256"]
    assert before["quality_pass"] is after["quality_pass"] is None


def test_held_out_review_requires_explicit_legacy_contract(reviewer, tmp_path):
    path = report(reviewer, tmp_path, [("case-13", "error", None, [])], split="held_out")
    with pytest.raises(ValueError, match="development annotations only"):
        reviewer.review(path, split="held_out")
    result = reviewer.review(path, split="held_out", contract="legacy-v1")
    assert result["review_contract"] == "legacy-v1" and result["expected_status_counts"] is None
    with pytest.raises(ValueError, match="Unknown review contract"):
        reviewer.review(path, split="held_out", contract="unknown")


def test_new_expectations_are_not_read_by_generation_or_exposed_in_prompts(reviewer, tmp_path, monkeypatch):
    annotations = reviewer.suite.load(reviewer.suite.SUITE / reviewer.ANNOTATION_FILE)
    original_load = reviewer.suite.load
    def no_annotations(path):
        assert path.name not in (reviewer.ANNOTATION_FILE, "decision-contract.v2.json")
        return original_load(path)
    monkeypatch.setattr(reviewer.suite, "load", no_annotations)
    output = tmp_path / "baseline.jsonl"
    reviewer.suite.baseline(live=False, model="fake-model", split="development", limit=12, output=output)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    prompts = json.dumps([r["requests"] for r in rows[1:]])
    assert "expected_status" not in prompts and "EVALUATOR_ONLY_RAG" not in prompts
    for item in annotations["cases"]:
        for field in ("supported_scope", "unsupported_extension", "required_gap", "useful_checks"):
            if field in item:
                assert item[field] not in prompts


def test_cli_records_selected_contract_and_refuses_to_overwrite_review(reviewer, tmp_path, monkeypatch, capsys):
    path = report(reviewer, tmp_path, [("case-11", "ok", diagnosis("insufficient_evidence"), [])])
    destination = tmp_path / "review.json"
    monkeypatch.setattr("sys.argv", ["review.py", str(path), "--contract", "legacy-v1", "--output", str(destination)])
    reviewer.main()
    console = json.loads(capsys.readouterr().out)
    assert console["review_contract"] == "legacy-v1"
    assert console["unexpected_abstentions"] is None
    assert json.loads(destination.read_text())["review_contract_sha256"] == console["review_contract_sha256"]
    with pytest.raises(FileExistsError):
        reviewer.main()
