"""One bounded development experiment: matched runbooks to mechanism/check excerpts."""

import argparse
from hashlib import sha256
import importlib.util
import json
import math
import os
from pathlib import Path
import time

import psycopg
from psycopg.rows import dict_row

from incidentclue.rag.context import construct_query
from incidentclue.rag.corpus import RetrievalConfig, load_corpus
from incidentclue.rag.embeddings import LocalEmbedder, artifact_contract, checked_vector
from incidentclue.rag.storage import (
    _adapter, _vector, bound_sources, ingest, lookup, prepare_schema, require_schema,
)

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("representation_control_v2", HERE / "representation.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
baseline = control.baseline
ROOT = HERE.parents[1]
PROTOCOL = HERE / "runbook-protocol.v3.json"
CUTOFFS = (0.25, 0.30)


def prior_rows(report):
    expected = baseline.read(HERE / "calibration-development.v2.json")
    if sha256(report.read_bytes()).hexdigest() != expected["raw_report_sha256"]:
        raise ValueError("Archived v2 report identity mismatch")
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    if rows[-1].get("type") != "summary":
        raise ValueError("Incomplete archived v2 report")
    return {(r["case_id"], r["representation"], r["mode"], r["cutoff"]): r
            for r in rows if r["type"] == "retrieval"}


def compare(row, previous):
    key = (row["case_id"], row["representation"], row["mode"], row["cutoff"])
    expected = previous[key]
    for field in ("query", "query_metadata", "input_sha256", "service"):
        if row[field] != expected[field]:
            raise ValueError("Archived control input changed")
    left, right = row["result"]["retrieved_sources"], expected["result"]["retrieved_sources"]
    if len(left) != len(right):
        raise ValueError("Archived control length changed")
    for actual, original in zip(left, right, strict=True):
        a, b = dict(actual), dict(original)
        distance, prior_distance = a.pop("distance"), b.pop("distance")
        if a != b or ((distance is None) != (prior_distance is None)):
            raise ValueError("Archived control source changed")
        if distance is not None and not math.isclose(distance, prior_distance, abs_tol=1e-6, rel_tol=0):
            raise ValueError("Archived control distance changed")


def expand(corpus_id, service, ranked, config):
    """Input is all scope-filtered passages in exact distance/C-ID order."""
    documents, groups, seen = [], {}, set()
    for row in ranked:
        p = row["metadata"]
        if (p["status"] != "current" or not ({service, "global"} & set(p["services"]))
                or p["passage_id"] in seen or not math.isfinite(float(row["score"]))):
            raise ValueError("Invalid scoped candidate")
        seen.add(p["passage_id"])
        groups.setdefault(p["document_id"], []).append(row)
    for row in ranked:
        p = row["metadata"]
        if row["score"] <= config["max_distance"] and p["document_id"] not in {d["document_id"] for d in documents}:
            documents.append({"document_id": p["document_id"], "seed_passage_id": p["passage_id"],
                              "seed_distance": float(row["score"])})
    expanded = []
    for document in documents:
        siblings = groups[document["document_id"]]
        for section in ("Mechanism", "Checks"):
            matches = [r for r in siblings if r["metadata"]["section"].casefold() == section.casefold()]
            if len(matches) != 1:
                raise ValueError("Runbook requires exactly one mechanism and checks passage")
            expanded.extend(matches)
    result = bound_sources(corpus_id, expanded, config, mode="vector")
    return {"corpus_id": corpus_id, "mode": "runbook", "selected_documents": documents,
            "distance_semantics": "Each source has its own cosine distance; seed cutoff selects the document",
            "matched_passages": len(expanded), **result}


def runbook_lookup(connection, corpus, service, query, embedder):
    require_schema(connection)
    vector = checked_vector(embedder.query_vector(query))
    with connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        _adapter(connection)
        rows = connection.execute('''WITH eligible AS MATERIALIZED (
            SELECT passage_id, metadata, embedding FROM ic_rag.passages
            WHERE corpus_id=%s AND current AND (services @> ARRAY[%s]::text[]
                OR services @> ARRAY['global']::text[]))
            SELECT passage_id, metadata, embedding <=> %s AS score FROM eligible
            ORDER BY score ASC, passage_id COLLATE "C" ASC''',
            (corpus.corpus_id, service, _vector(vector))).fetchall()
        return expand(corpus.corpus_id, service, rows, corpus.contract["retrieval"])


def run(connection, model_directory, previous_report, output):
    if output.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Write evidence outside application Git")
    plan = baseline.read(PROTOCOL)
    if tuple(plan["cutoffs"]) != CUTOFFS or plan["maximum_retrieval_operations"] != 60:
        raise ValueError("Protocol mismatch")
    previous = prior_rows(previous_report)
    fixture = baseline.verify_frozen()
    with output.open("x", encoding="utf-8") as stream:
        def write(row):
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
        write({"type": "configuration", "protocol": plan,
               "protocol_sha256": sha256(PROTOCOL.read_bytes()).hexdigest(),
               "runner_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
               "helper_sha256": {n: sha256((HERE / n).read_bytes()).hexdigest()
                                  for n in ("retrieval.py", "representation.py")},
               "previous_report_sha256": sha256(previous_report.read_bytes()).hexdigest(),
               "fixture_sha256": fixture, "provider_calls": 0, "held_out_scored": 0})
        started = time.perf_counter()
        try:
            backend = LocalEmbedder(model_directory)
            shared = baseline.CachedEmbedder(backend)
            queries = [(case, incident, digest, *construct_query(incident, backend.model.token_count))
                       for case, incident, digest in baseline.development_inputs()]
            prepare_schema(connection)
            initial = load_corpus(ROOT, artifact_contract(), RetrievalConfig(max_distance=0.2))
            raw, publications = [], []
            for cutoff in CUTOFFS:
                embedder = control.RepresentationEmbedder(shared, initial.passages, "title_context")
                corpus = load_corpus(ROOT, embedder.artifact, RetrievalConfig(max_distance=cutoff))
                publication = ingest(connection, corpus, embedder)
                publications.append(publication)
                write({"type": "publication", **publication})
                for case, incident, digest, query, info in queries:
                    for mode in ("vector", "runbook"):
                        result = (lookup(connection, corpus.corpus_id, incident.service, query, embedder=embedder)
                                  if mode == "vector" else runbook_lookup(connection, corpus, incident.service, query, embedder))
                        row = {"type": "retrieval", "case_id": case, "representation": "title_context",
                               "mode": mode, "cutoff": cutoff, "input_sha256": digest,
                               "service": incident.service, "query": query, "query_metadata": info, "result": result}
                        write(row)
                        raw.append(row)
                        if mode == "vector":
                            compare(row, previous)
            body = control.RepresentationEmbedder(shared, initial.passages, "body")
            publication = ingest(connection, initial, body)
            publications.append(publication)
            write({"type": "publication", **publication})
            for case, incident, digest, query, info in queries:
                result = control.lexical_or(connection, initial.corpus_id, incident.service, query, initial.contract["retrieval"])
                row = {"type": "retrieval", "case_id": case, "representation": "body", "mode": "lexical_or",
                       "cutoff": None, "input_sha256": digest, "service": incident.service,
                       "query": query, "query_metadata": info, "result": result}
                write(row)
                raw.append(row)
                compare(row, previous)
            if len(raw) != 60 or len(shared.passages) != 2 or len(shared.queries) != 12:
                raise ValueError("Execution budget mismatch")
            # Frozen annotations enter only after every retrieval finishes.
            annotations = {r["id"]: r for r in baseline.read(HERE / "labels.json")["labels"]}
            scored = control.score(raw, {p["passage_id"]: p for p in initial.passages}, annotations)
            for row in scored:
                write(row)
            lexical = baseline.summarize([r for r in scored if r["mode"] == "lexical_or"])
            summaries = {mode: {str(c): baseline.summarize([r for r in scored if r["mode"] == mode and r["cutoff"] == c])
                               for c in CUTOFFS} for mode in ("vector", "runbook")}
            summary = {"type": "summary", "fixture_sha256": fixture, "split": "development",
                       "controls": summaries["vector"], "candidate": summaries["runbook"], "lexical_or": lexical,
                       "selected_cutoff": baseline.select_candidate(summaries["runbook"], lexical),
                       "retrieval_operations": len(raw), "control_matches": 36,
                       "passage_embedding_batches": len(shared.passages), "query_vectors": len(shared.queries),
                       "publications": publications, "elapsed_seconds": time.perf_counter() - started,
                       "provider_calls": 0, "held_out_scored": 0, "runtime_promoted": False,
                       "diagnosis_quality_assessed": False, "latency_note": "Cached repeats; not a latency benchmark"}
            write(summary)
            return summary
        except BaseException as error:
            write({"type": "failure", "error_type": type(error).__name__})
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    baseline.block_external_sockets()
    try:
        parameters = baseline.connection_parameters(os.environ.get("INCIDENTCLUE_RAG_TEST_DATABASE_URL", ""))
        with psycopg.connect(**parameters, autocommit=True, row_factory=dict_row) as connection:
            result = run(connection, args.model_dir, args.baseline_report, args.output)
        print(json.dumps({k: result[k] for k in ("selected_cutoff", "retrieval_operations", "control_matches", "provider_calls")}))
        return 0
    except Exception as error:
        print(f"Runbook comparison failed ({type(error).__name__}); preserve evidence.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
