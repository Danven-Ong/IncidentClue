"""Offline decision/reference checks; semantic support still requires manual review."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from incidentclue.diagnosis import validate_evidence_references
from incidentclue.investigation import merge_evidence
from incidentclue.schemas import Diagnosis, Evidence, Incident

import suite


def review(path: Path, *, split="development"):
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
                 and not any(r["answerable_case_abstained"] for r in completed)
                 and all(r["missing_evidence_present"] is not False for r in completed))
    return {"source_report_sha256": sha256(path.read_bytes()).hexdigest(),
        "reviewer_sha256": suite.digest(Path(__file__)),
        "fixture_sha256": frozen["fixture_sha256"], "runtime": header.get("runtime"),
        "mode": header["mode"], "split": split, "planned_cases": header["case_count"],
        "attempted_cases": len(outcomes), "unattempted_ids": unattempted,
        "accepted_diagnoses": len(completed), "execution_errors": errors,
        "required_abstentions": required_total, "required_abstentions_assessed": len(assessed_required),
        "required_abstentions_passed": sum(r["required_abstention_ok"] is True for r in required),
        "required_abstentions_failed": sum(r["required_abstention_ok"] is False for r in required),
        "required_abstentions_unassessed": required_total - len(assessed_required),
        "answerable_cases_abstained": sum(r["answerable_case_abstained"] is True for r in outcomes),
        "automated_checks_passed": automatic,
        "semantic_support": "manual_review_required", "quality_pass": False if not automatic else None,
        "outcomes": outcomes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--split", choices=["development", "held_out"], default="development")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = review(args.report, split=args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in ("planned_cases", "accepted_diagnoses", "execution_errors",
        "required_abstentions_failed", "required_abstentions_unassessed", "answerable_cases_abstained",
        "automated_checks_passed", "quality_pass")}))


if __name__ == "__main__":
    main()
