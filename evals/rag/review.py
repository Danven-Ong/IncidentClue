"""Offline decision/reference checks; semantic support still requires manual review."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from incidentclue.diagnosis import validate_evidence_references
from incidentclue.investigation import merge_evidence
from incidentclue.schemas import Diagnosis, Evidence, Incident

import suite


DEFAULT_CONTRACT = "decision-v2"
CONTRACTS = (DEFAULT_CONTRACT, "legacy-v1")
ANNOTATION_FILE = "decision-expectations-development.v1.json"


def decision_contract(frozen, split, contract):
    """Load evaluator-only expectations; never infer a positive target from labels."""
    if contract not in CONTRACTS:
        raise ValueError("Unknown review contract")
    identity = {"review_contract": contract, "fixture_sha256": frozen["fixture_sha256"],
                "reviewer_sha256": suite.digest(Path(__file__))}
    if contract == "legacy-v1":
        return None, {**identity, "decision_annotations": None,
                      "review_contract_sha256": sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()}
    if split != "development":
        raise ValueError("decision-v2 has development annotations only; use legacy-v1 for held-out review")
    manifest_path = suite.SUITE / "decision-contract.v2.json"
    manifest = suite.load(manifest_path)
    manifest_fields = {"review_contract", "annotation_file", "annotation_sha256", "fixture_sha256",
                       "split", "annotation_version", "annotation_status"}
    if not isinstance(manifest, dict) or set(manifest) != manifest_fields or (
        manifest["review_contract"] != contract or manifest["annotation_file"] != ANNOTATION_FILE
        or manifest["fixture_sha256"] != frozen["fixture_sha256"] or manifest["split"] != split
        or type(manifest["annotation_version"]) is not int or manifest["annotation_version"] != 1
    ):
        raise ValueError("Decision contract manifest does not match version, split or fixtures")
    annotation_path = suite.SUITE / ANNOTATION_FILE
    annotation_hash = suite.digest(annotation_path)
    if annotation_hash != manifest["annotation_sha256"]:
        raise ValueError("Decision annotation hash does not match its version manifest")
    annotations = suite.load(annotation_path)
    annotation_fields = {"annotation_version", "status", "fixture_sha256", "split", "runtime_use_permitted", "cases"}
    if not isinstance(annotations, dict) or set(annotations) != annotation_fields or (
        type(annotations["annotation_version"]) is not int or annotations["annotation_version"] != 1
        or annotations["status"] != manifest["annotation_status"]
        or annotations["status"] != "proposed_assistant_annotations_not_independent_ground_truth"
        or annotations["fixture_sha256"] != frozen["fixture_sha256"] or annotations["split"] != split
        or annotations["runtime_use_permitted"] is not False or not isinstance(annotations["cases"], list)
    ):
        raise ValueError("Decision annotations have invalid version, provenance, split or fixture identity")
    case_ids = {c["id"] for c in suite.load(suite.SUITE / "cases.json")["cases"] if c["split"] == split}
    labels = {label["id"]: label for label in suite.load(suite.SUITE / "labels.json")["labels"]}
    expected = {}
    for item in annotations["cases"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or (
            item["id"] not in case_ids or item["id"] in expected
        ):
            raise ValueError("Decision annotation IDs must be unique and in the selected split")
        status = item.get("expected_status")
        if status not in ("hypothesis", "insufficient_evidence"):
            raise ValueError("Decision annotation has an unknown expected status")
        scope_fields = ({"supported_scope", "unsupported_extension"} if status == "hypothesis"
                        else {"required_gap", "useful_checks"})
        if set(item) != {"id", "expected_status", *scope_fields} or any(
            not isinstance(item[field], str) or not item[field].strip() for field in scope_fields
        ):
            raise ValueError("Decision annotation must describe nonblank claim scope or deciding gaps")
        if labels[item["id"]]["must_abstain"] and status != "insufficient_evidence":
            raise ValueError("Decision annotation conflicts with a frozen required abstention")
        expected[item["id"]] = status
    if set(expected) != case_ids:
        raise ValueError("Decision annotations must cover every case in the selected split")
    identity.update({"manifest_sha256": suite.digest(manifest_path), "annotation_sha256": annotation_hash})
    return expected, {**identity,
        "review_contract_sha256": sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
        "decision_annotations": {"version": annotations["annotation_version"], "split": split,
                                 "status": annotations["status"], "sha256": annotation_hash}}


def review(path: Path, *, split="development", contract=DEFAULT_CONTRACT):
    """Review one immutable report, without selecting newer reruns or making calls."""
    frozen = suite.validate()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if not rows or rows[0].get("type") != "configuration":
        raise ValueError("A baseline configuration header is required")
    header = rows[0]
    if type(header.get("case_count")) is not int or not 1 <= header["case_count"] <= 12:
        raise ValueError("Planned case count must be between 1 and 12")
    if header.get("split") != split or header.get("fixture_sha256") != frozen["fixture_sha256"]:
        raise ValueError("Report split or fixture version does not match the selected suite")
    if header.get("mode") not in ("live", "fake_smoke"):
        raise ValueError("Report mode must distinguish live from fake execution")
    expected, contract_details = decision_contract(frozen, split, contract)
    cases = {c["id"]: c for c in suite.load(suite.SUITE / "cases.json")["cases"] if c["split"] == split}
    labels = {x["id"]: x for x in suite.load(suite.SUITE / "labels.json")["labels"]}
    outcomes, seen, unattempted, stopped = [], set(), [], False
    for row in rows[1:]:
        if stopped:
            raise ValueError("Rows cannot follow a stopped report footer")
        if row.get("type") == "stopped":
            remaining = row.get("remaining_ids", [])
            if not isinstance(remaining, list) or not all(isinstance(x, str) for x in remaining):
                raise ValueError("Invalid unattempted case list")
            if unattempted or len(remaining) != len(set(remaining)) or not set(remaining).issubset(cases):
                raise ValueError("Unattempted cases must be unique and in the selected split")
            unattempted = remaining
            stopped = True
            continue
        ident = row.get("id")
        if ident not in cases or ident in seen:
            raise ValueError("Cases must be unique and belong to the selected split")
        seen.add(ident)
        case_path = suite.SUITE / cases[ident]["path"]
        if row.get("input_sha256") != suite.digest(case_path):
            raise ValueError("Saved input hash does not match the frozen case")
        label = labels[ident]
        result = {"id": ident, "category": label["category"], "must_abstain": label["must_abstain"],
                  "execution_completed": row.get("status") == "ok",
                  "reference_membership_ok": None, "required_abstention_ok": None,
                  "answerable_case_abstained": None, "missing_evidence_present": None}
        result.update(expected_status=expected[ident] if expected is not None else None,
                      expected_status_ok=None)
        if row.get("status") == "ok":
            diagnosis = Diagnosis.model_validate(row["diagnosis"])
            payload = suite.load(case_path)
            incident = Incident.model_validate(payload["incident"])
            evidence = incident.evidence
            for event in row.get("events", []):
                output = event.get("result", {})
                if output.get("status") == "ok":
                    evidence = merge_evidence(evidence, [Evidence.model_validate(e) for e in output.get("evidence", [])])
            incident = incident.model_copy(update={"evidence": evidence})
            try:
                validate_evidence_references(incident, diagnosis)
                result["reference_membership_ok"] = True
            except ValueError:
                result["reference_membership_ok"] = False
            abstained = diagnosis.status == "insufficient_evidence"
            result["diagnosis_status"] = diagnosis.status
            if expected is not None:
                result["expected_status_ok"] = diagnosis.status == expected[ident]
            result["required_abstention_ok"] = abstained if label["must_abstain"] else None
            result["answerable_case_abstained"] = abstained if label["category"] == "answerable" else None
            result["missing_evidence_present"] = any(text.strip() for text in diagnosis.missing_evidence) if abstained else None
        elif row.get("status") != "error":
            raise ValueError("Unknown execution status")
        outcomes.append(result)
    if seen.intersection(unattempted) or header.get("case_count") != len(outcomes) + len(unattempted):
        raise ValueError("Planned, attempted and unattempted case counts disagree")
    required = [r for r in outcomes if r["must_abstain"]]
    required_total = len(required) + sum(labels[ident]["must_abstain"] for ident in unattempted)
    assessed_required = [r for r in required if r["execution_completed"]]
    completed = [r for r in outcomes if r["execution_completed"]]
    errors = sum(not r["execution_completed"] for r in outcomes)
    automatic = (not errors and not unattempted and all(r["reference_membership_ok"] for r in completed)
                 and all(r["required_abstention_ok"] for r in assessed_required)
                 and all(r["expected_status_ok"] is not False for r in completed)
                 and not any(r["answerable_case_abstained"] for r in completed)
                 and all(r["missing_evidence_present"] is not False for r in completed))
    status_counts = None
    if expected is not None:
        planned_ids = seen | set(unattempted)
        status_counts = {}
        for status in ("hypothesis", "insufficient_evidence"):
            planned = sum(expected[ident] == status for ident in planned_ids)
            assessed = [r for r in completed if r["expected_status"] == status]
            status_counts[status] = {"planned": planned, "assessed": len(assessed),
                "matched": sum(r["expected_status_ok"] for r in assessed),
                "mismatched": sum(not r["expected_status_ok"] for r in assessed),
                "unassessed": planned - len(assessed)}
    return {**contract_details, "source_report_sha256": sha256(path.read_bytes()).hexdigest(),
        "fixture_sha256": frozen["fixture_sha256"], "runtime": header.get("runtime"),
        "mode": header["mode"], "split": split, "planned_cases": header["case_count"],
        "attempted_cases": len(outcomes), "unattempted_ids": unattempted,
        "accepted_diagnoses": len(completed), "execution_errors": errors,
        "required_abstentions": required_total, "required_abstentions_assessed": len(assessed_required),
        "required_abstentions_passed": sum(r["required_abstention_ok"] is True for r in required),
        "required_abstentions_failed": sum(r["required_abstention_ok"] is False for r in required),
        "required_abstentions_unassessed": required_total - len(assessed_required),
        "answerable_cases_abstained": sum(r["answerable_case_abstained"] is True for r in outcomes),
        "expected_status_counts": status_counts,
        "unexpected_abstentions": status_counts["hypothesis"]["mismatched"] if status_counts is not None else None,
        "unexpected_hypotheses": status_counts["insufficient_evidence"]["mismatched"] if status_counts is not None else None,
        "automated_checks_passed": automatic,
        "semantic_support": "manual_review_required", "quality_pass": False if not automatic else None,
        "outcomes": outcomes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--split", choices=["development", "held_out"], default="development")
    parser.add_argument("--contract", choices=CONTRACTS, default=DEFAULT_CONTRACT,
                        help="decision-v2 adds explicit development targets; legacy-v1 retains prior checks")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = review(args.report, split=args.split, contract=args.contract)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in ("review_contract", "review_contract_sha256",
        "planned_cases", "accepted_diagnoses", "execution_errors", "unexpected_abstentions", "unexpected_hypotheses",
        "required_abstentions_failed", "required_abstentions_unassessed", "answerable_cases_abstained",
        "automated_checks_passed", "quality_pass")}))


if __name__ == "__main__":
    main()
