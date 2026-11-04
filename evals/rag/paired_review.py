"""Offline structural checks and an unscored semantic-review worksheet for paired runs."""

import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

from incidentclue.diagnosis import validate_saved_diagnosis
from incidentclue.investigation import merge_evidence
from incidentclue.schemas import Evidence
from incidentclue.schemas import Incident

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location("paired_review_target", HERE / "paired.py")
paired = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paired)


def review(path):
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Paired report exceeds review size limit")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    header = rows[0]
    if (header.get("type") != "configuration" or header.get("protocol_sha256") != sha256(paired.PROTOCOL.read_bytes()).hexdigest()
            or header.get("fixture_sha256") != paired.baseline.verify_frozen()):
        raise ValueError("Report protocol/fixture identity mismatch")
    records = [r for r in rows if r["type"] == "investigation"]
    if [(r["case_id"], r["arm"]) for r in records] != paired.schedule()[:len(records)] or len(records) > 12:
        raise ValueError("Report schedule is not a unique protocol prefix")
    manifest = paired.baseline.read(HERE / "decision-contract.v2.json")
    annotation_path = HERE / manifest["annotation_file"]
    if (manifest["annotation_file"] != "decision-expectations-development.v1.json"
            or sha256(annotation_path.read_bytes()).hexdigest() != manifest["annotation_sha256"]
            or manifest["fixture_sha256"] != header["fixture_sha256"]):
        raise ValueError("Reference annotation identity mismatch")
    annotations = {r["id"]: r for r in paired.baseline.read(annotation_path)["cases"]}
    results, by_case = [], {}
    for record in records:
        case, arm = record["case_id"], record["arm"]
        payload = paired.baseline.read(HERE / f"inputs/{case}.json")
        if paired.identity(payload) != record["input_sha256"]:
            raise ValueError("Recorded input changed")
        valid, error = False, None
        support_flags = {"support_record_available": False, "support_internal_consistency_valid": None,
                         "support_excerpt_membership_valid": None, "semantic_support_verified": None}
        if record["status"] == "ok":
            try:
                observations = Incident.model_validate(payload["incident"]).evidence
                returned_sources = []
                for event in record["events"]:
                    if "result" not in event:
                        continue
                    if event.get("name") == "get_runbook_context":
                        if event["result"].get("evidence"):
                            raise ValueError("Runbook supplied observations")
                        returned_sources = event["result"].get("retrieved_sources", [])
                    elif event["result"].get("status") == "ok":
                        observations = merge_evidence(observations, [Evidence.model_validate(e) for e in event["result"].get("evidence", [])])
                if ([e.model_dump(mode="json") for e in observations] != record["evidence"]
                        or returned_sources != record["retrieved_sources"]):
                    raise ValueError("Final evidence/sources differ from observed tool outputs")
                enriched = Incident.model_validate({**payload["incident"], "evidence": record["evidence"]})
                support_flags = paired.suite.saved_support_checks(enriched, record["diagnosis"])
                diagnosis = validate_saved_diagnosis(enriched, json.dumps(record["diagnosis"]), sources=record["retrieved_sources"])
                valid = True
            except ValueError as failure:
                error = type(failure).__name__
        if arm == "no_rag" and record["retrieved_sources"]:
            valid, error = False, "NoRagContainsSources"
        counts_valid = (record["model_attempts"] == len(record["requests"]) == len(record["attempts"])
                        and record["model_attempts"] <= 3 and record["tool_attempts"] <= 2
                        and sum(c.get("tool_name") == "get_runbook_context" for c in record["node_attempts"]) <= 1)
        observed = [r for r in record["provider_usage"] if r is not None]
        cause = record.get("diagnosis", {}).get("probable_cause")
        result = {"case_id": case, "arm": arm, "execution_status": record["status"],
                  "structure_and_citation_membership_valid": valid, "validation_error_type": error,
                  "generation_diagnosis_contract_valid": paired.suite.recorded_generation_contract_valid(record),
                  "generation_contract_version": 2,
                  **support_flags,
                  "budgets_and_accounting_valid": counts_valid,
                  "expected_status": annotations[case]["expected_status"],
                  "expected_status_matched": diagnosis.status == annotations[case]["expected_status"] if valid else None,
                  "reference_scope": annotations[case], "elapsed_seconds": record["elapsed_seconds"],
                  "retrieval_attempted": record["retrieval_attempted"],
                  "retrieval_completed": record["retrieval_completed"],
                  "returned_source_count": len(record["retrieved_sources"]),
                  "observed_usage_requests": len(observed),
                  "usage_unknown_requests": sum(a.get("usage") is None for a in record["attempts"]),
                  "known_prompt_tokens": sum(u.get("prompt_tokens", 0) or 0 for u in observed),
                  "known_completion_tokens": sum(u.get("completion_tokens", 0) or 0 for u in observed),
                  "manual_review": {"reviewer_identity": None, "reviewer_role": None,
                      "causal_claims": [{"claim": cause, "supporting_observation_ids": [], "supported": None}] if cause else [],
                      "review_all_answer_fields_for_implicit_causal_claims": True,
                      "document_dependent_claims_and_exact_support": [],
                      "all_document_claims_supported_and_cited": None,
                      "unsupported_causal_claim_count": None,
                      "abstention_or_supported_hypothesis_appropriate": None,
                      "useful_read_only_checks": [], "unsafe_or_unsupported_checks": [],
                      "semantic_pass": None}}
        results.append(result)
        by_case.setdefault(case, {})[arm] = record
    pairs = []
    for case in paired.CASE_IDS:
        arms = by_case.get(case, {})
        complete = set(arms) == {"no_rag", "rag"} and all(r["status"] == "ok" for r in arms.values())
        pair = {"case_id": case, "complete_pair": complete, "supported_answer_retained": None,
                "correctly_sourced_useful_checks_added": None, "regression": None, "review_notes": None}
        if complete:
            pair["final_observations_equal"] = arms["no_rag"]["evidence"] == arms["rag"]["evidence"]
            pair["new_check_texts_not_yet_judged_useful"] = [c for c in arms["rag"]["diagnosis"]["next_checks"]
                if c not in arms["no_rag"]["diagnosis"]["next_checks"]]
        pairs.append(pair)
    return {"schema_version": 2, "raw_report_sha256": sha256(path.read_bytes()).hexdigest(),
            "reviewer_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "reference_annotation_sha256": manifest["annotation_sha256"],
            "mode": header["mode"], "quality_pass": None, "results": results, "pairs": pairs,
            "warning": "Structure/status matches do not establish semantic support. Fake mode measures no model quality. Reviewer must inspect raw answers, observations and excerpts; assistant review is not independent human review."}


def worksheet(report, raw_path):
    rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    records = {(r["case_id"], r["arm"]): r for r in rows if r["type"] == "investigation"}
    lines = ["# Paired RAG diagnostic review", "", f"Mode: {report['mode']}. Semantic quality: **unassessed**.", "",
             "Review every causal claim against incident observations; runbooks can support general guidance only.",
             "Record reviewer identity and claim/excerpt judgments in the companion JSON. Empty review fields mean unassessed, not pass.", ""]
    for result in report["results"]:
        record = records[(result["case_id"], result["arm"])]
        lines += [f"## {result['case_id']} / {result['arm']}", "",
                  f"Execution: {record['status']}; expected status: {result['expected_status']}; membership valid: {result['structure_and_citation_membership_valid']}.", "",
                  "Reference scope (assistant-proposed, not independent ground truth):", "", "```json",
                  json.dumps(result["reference_scope"], indent=2), "```", "", "Answer:", "", "```json",
                  json.dumps(record.get("diagnosis"), indent=2), "```", "", "Available observations:", "", "```json",
                  json.dumps(record["evidence"], indent=2), "```", "", "Retrieved source excerpts:", "", "```json",
                  json.dumps(record["retrieved_sources"], indent=2), "```", "",
                  "Manual judgments: causal support [unassessed]; document support [unassessed]; useful checks [unassessed]; abstention [unassessed].", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    markdown = args.output.with_suffix(".md")
    if (args.output.resolve().is_relative_to(ROOT.resolve()) or markdown.resolve().is_relative_to(ROOT.resolve())
            or args.output == markdown or args.output.exists() or markdown.exists()):
        parser.error("Use two new output paths outside application Git")
    result = review(args.report)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    with markdown.open("x", encoding="utf-8") as stream:
        stream.write(worksheet(result, args.report))
    print(json.dumps({"review_json": str(args.output), "worksheet": str(markdown), "quality_pass": None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
