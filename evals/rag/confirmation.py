"""Single frozen held-out confirmation of the selected runbook policy. No generation."""

import argparse
from hashlib import sha256
import importlib.util
import json
import math
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from incidentclue.rag.context import construct_query
from incidentclue.rag.corpus import RetrievalConfig, load_corpus
from incidentclue.rag.embeddings import LocalEmbedder, artifact_contract
from incidentclue.rag.storage import ingest, lookup, prepare_schema
from incidentclue.schemas import Incident

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("frozen_runbook_v3", HERE / "runbook.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
baseline = policy.baseline
ROOT = HERE.parents[1]
PROTOCOL = HERE / "confirmation-protocol.v1.json"


def held_out_inputs(root=ROOT):
    cases = [r for r in baseline.read(root / "evals/rag/cases.json")["cases"] if r["split"] == "held_out"]
    if len(cases) != 12 or len({r["id"] for r in cases}) != 12:
        raise ValueError("Expected twelve unique held-out cases")
    result = []
    for case in cases:
        if case["path"] != f"inputs/{case['id']}.json":
            raise ValueError("Unexpected held-out input path")
        path = root / "evals/rag" / case["path"]
        result.append((case["id"], Incident.model_validate(baseline.read(path)["incident"]), sha256(path.read_bytes()).hexdigest()))
    return result


def confirmation_pass(candidate, lexical):
    return (candidate["assessed"] == 12 and candidate["answerable_cases"] == 6
            and candidate["no_guidance_cases"] == 2 and candidate["answerable_hits"] >= 5
            and candidate["no_guidance_false_positives"] == 0 and candidate["scope_version_violations"] == 0
            and candidate["mrr_at_3"] >= lexical["mrr_at_3"])


def prior(report):
    record = baseline.read(HERE / "calibration-development.v3.json")
    if record["summary"]["selected_cutoff"] != 0.3 or sha256(report.read_bytes()).hexdigest() != record["raw_report_sha256"]:
        raise ValueError("Selected development report mismatch")
    for filename, expected in {"runbook.py": record["runner_sha256"], **record["helper_sha256"]}.items():
        if sha256((HERE / filename).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen development implementation changed")
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    if rows[-1].get("type") != "summary":
        raise ValueError("Incomplete development report")
    return {(r["case_id"], r["representation"], r["mode"], r["cutoff"]): r for r in rows if r["type"] == "retrieval"}


def run(connection, model_directory, archived, output):
    if output.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Evidence must remain outside application Git")
    plan = baseline.read(PROTOCOL)
    if plan["fixed_cutoff"] != 0.3 or plan["maximum_retrieval_operations"] != 60:
        raise ValueError("Confirmation protocol mismatch")
    previous = prior(archived)
    fixture = baseline.verify_frozen()
    with output.open("x", encoding="utf-8") as stream:
        def write(row):
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
        write({"type": "configuration", "protocol": plan,
               "protocol_sha256": sha256(PROTOCOL.read_bytes()).hexdigest(),
               "runner_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
               "development_report_sha256": sha256(archived.read_bytes()).hexdigest(),
               "fixture_sha256": fixture, "provider_calls": 0})
        try:
            backend = LocalEmbedder(model_directory)
            shared = baseline.CachedEmbedder(backend)
            prepare_schema(connection)
            body_corpus = load_corpus(ROOT, artifact_contract(), RetrievalConfig(max_distance=0.2))
            body = policy.control.RepresentationEmbedder(shared, body_corpus.passages, "body")
            title = policy.control.RepresentationEmbedder(shared, body_corpus.passages, "title_context")
            candidate_corpus = load_corpus(ROOT, title.artifact, RetrievalConfig(max_distance=0.3))
            for corpus, embedder in ((body_corpus, body), (candidate_corpus, title)):
                write({"type": "publication", **ingest(connection, corpus, embedder)})
            raw, controls = [], 0
            for split in ("development", "held_out"):
                # Held-out files are first interpreted only after all controls reproduce.
                inputs = baseline.development_inputs() if split == "development" else held_out_inputs()
                for case, incident, digest in inputs:
                    query, info = construct_query(incident, backend.model.token_count)
                    modes = ("runbook", "lexical_or") if split == "development" else ("runbook", "lexical_or", "lexical")
                    for mode in modes:
                        if mode == "runbook":
                            result = policy.runbook_lookup(connection, candidate_corpus, incident.service, query, title)
                        elif mode == "lexical_or":
                            result = policy.control.lexical_or(connection, body_corpus.corpus_id, incident.service, query, body_corpus.contract["retrieval"])
                        else:
                            result = lookup(connection, body_corpus.corpus_id, incident.service, query, mode="lexical")
                        row = {"type": "retrieval", "split": split, "case_id": case,
                               "representation": "title_context" if mode == "runbook" else "body",
                               "mode": mode, "cutoff": 0.3 if mode == "runbook" else None,
                               "input_sha256": digest, "service": incident.service, "query": query,
                               "query_metadata": info, "result": result}
                        write(row)
                        raw.append(row)
                        if split == "development":
                            policy.compare(row, previous)
                            if row["mode"] == "runbook":
                                seeds = previous[(case, "title_context", "runbook", 0.3)]["result"]["selected_documents"]
                                if len(result["selected_documents"]) != len(seeds):
                                    raise ValueError("Document seed count changed")
                                for actual, expected in zip(result["selected_documents"], seeds, strict=True):
                                    if (actual["document_id"] != expected["document_id"]
                                            or actual["seed_passage_id"] != expected["seed_passage_id"]
                                            or not math.isclose(actual["seed_distance"], expected["seed_distance"], abs_tol=1e-6, rel_tol=0)):
                                        raise ValueError("Document seed control changed")
                            controls += 1
                if split == "development" and controls != 24:
                    raise ValueError("Control budget mismatch")
            if len(raw) != 60 or len(shared.passages) != 2 or len(shared.queries) > 24:
                raise ValueError("Confirmation budget mismatch")
            selected = [r for r in raw if r["split"] == "held_out"]
            annotations = {r["id"]: r for r in baseline.read(HERE / "labels.json")["labels"]}
            scored = policy.control.score(selected, {p["passage_id"]: p for p in body_corpus.passages}, annotations)
            for row in scored:
                write(row)
            summaries = {mode: baseline.summarize([r for r in scored if r["mode"] == mode])
                         for mode in ("runbook", "lexical_or", "lexical")}
            summary = {"type": "summary", "split": "held_out", "fixed_cutoff": 0.3,
                       "metrics": summaries, "confirmation_pass": confirmation_pass(summaries["runbook"], summaries["lexical_or"]),
                       "retrieval_operations": len(raw), "development_control_matches": controls,
                       "held_out_cases_scored": 12, "passage_vectors": 72, "query_vectors": len(shared.queries),
                       "provider_calls": 0, "runtime_promoted": False, "diagnosis_quality_assessed": False}
            write(summary)
            return summary
        except BaseException as error:
            write({"type": "failure", "error_type": type(error).__name__})
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--development-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    baseline.block_external_sockets()
    try:
        parameters = baseline.connection_parameters(os.environ.get("INCIDENTCLUE_RAG_TEST_DATABASE_URL", ""))
        with psycopg.connect(**parameters, autocommit=True, row_factory=dict_row) as connection:
            result = run(connection, args.model_dir, args.development_report, args.output)
        print(json.dumps({k: result[k] for k in ("confirmation_pass", "held_out_cases_scored", "development_control_matches", "provider_calls")}))
        return 0
    except Exception as error:
        print(f"Confirmation failed ({type(error).__name__}); preserve evidence.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
