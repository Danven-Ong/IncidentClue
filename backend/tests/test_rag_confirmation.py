"""Frozen confirmation gates and split boundaries, without reading held-out fixtures."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("confirmation_eval", ROOT / "evals/rag/confirmation.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_confirmation_gate_requires_every_predeclared_condition():
    passing = {"assessed": 12, "answerable_cases": 6, "no_guidance_cases": 2,
               "answerable_hits": 5, "no_guidance_false_positives": 0,
               "scope_version_violations": 0, "mrr_at_3": 0.7}
    comparator = {"mrr_at_3": 0.7}
    assert evaluation.confirmation_pass(passing, comparator)
    for field, bad in (("assessed", 11), ("answerable_cases", 5), ("no_guidance_cases", 1),
                       ("answerable_hits", 4), ("no_guidance_false_positives", 1),
                       ("scope_version_violations", 1), ("mrr_at_3", 0.699)):
        candidate = {**passing, field: bad}
        assert not evaluation.confirmation_pass(candidate, comparator)


def test_input_reader_opens_only_requested_split_in_synthetic_test_tree(tmp_path):
    directory = tmp_path / "evals/rag"
    (directory / "inputs").mkdir(parents=True)
    cases = [{"id": f"case-{i}", "path": f"inputs/case-{i}.json", "split": "held_out"} for i in range(12)]
    cases.append({"id": "forbidden", "path": "does-not-exist", "split": "development"})
    (directory / "cases.json").write_text(json.dumps({"cases": cases}), encoding="utf-8")
    for case in cases[:12]:
        (directory / case["path"]).write_text(json.dumps({"incident": {
            "incident_id": case["id"], "service": "test", "summary": "Synthetic boundary test", "evidence": []}}), encoding="utf-8")
    result = evaluation.held_out_inputs(tmp_path)
    assert len(result) == 12 and "forbidden" not in {r[0] for r in result}


def test_duplicate_or_escaping_case_paths_rejected_before_input_read(tmp_path):
    directory = tmp_path / "evals/rag"
    directory.mkdir(parents=True)
    cases = [{"id": f"case-{i}", "path": f"inputs/case-{i}.json", "split": "held_out"} for i in range(12)]
    duplicate = deepcopy(cases)
    duplicate[-1] = duplicate[0]
    for altered in (duplicate, [{**cases[0], "path": "../../private"}, *cases[1:]]):
        (directory / "cases.json").write_text(json.dumps({"cases": altered}), encoding="utf-8")
        with pytest.raises(ValueError):
            evaluation.held_out_inputs(tmp_path)


def test_changed_development_report_rejected(tmp_path):
    report = tmp_path / "not-original.jsonl"
    report.write_text('{}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="report mismatch"):
        evaluation.prior(report)
