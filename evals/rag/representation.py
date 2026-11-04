"""Bounded development comparison of title-prefixed embeddings and lexical OR."""

from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import math
import os
from pathlib import Path
import time
import argparse

import psycopg
from psycopg.rows import dict_row

from incidentclue.rag.corpus import RetrievalConfig, identity, load_corpus
from incidentclue.rag.context import construct_query
from incidentclue.rag.embeddings import LocalEmbedder, artifact_contract
from incidentclue.rag.storage import bound_sources, ingest, lookup, prepare_schema, require_schema

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("retrieval_baseline_v1", HERE / "retrieval.py")
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)
ROOT = HERE.parents[1]
PROTOCOL = HERE / "representation-protocol.v2.json"


def protocol():
    value = baseline.read(PROTOCOL)
    if tuple(value["cutoffs"]) != baseline.CUTOFFS or value["maximum_searches"] != 312:
        raise ValueError("Protocol and evaluator disagree")
    return value


def representation_artifact(name):
    artifact = deepcopy(artifact_contract())
    if name == "title_context":
        artifact["embedding_policy"] = "fastembed-title-passage-evaluation-v2"
        artifact["representation"] = {"format": "title-newline-passage-v2",
                                      "utf8_bytes": 2000, "tokens": 200}
    elif name != "body":
        raise ValueError("Unknown representation")
    return artifact


def representation_inputs(passages, name):
    inputs = {}
    for passage in passages:
        text = passage["text"]
        value = f"{passage['title']}\n{text}" if name == "title_context" else text
        if len(value.encode("utf-8")) > 2000:
            raise ValueError("Candidate embedding input exceeds byte limit")
        if text in inputs and inputs[text] != value:
            raise ValueError("Ambiguous identical passage text with different document titles")
        inputs[text] = value
    return inputs


class RepresentationEmbedder:
    def __init__(self, shared, passages, name):
        self.shared = shared
        self.artifact = representation_artifact(name)
        self.inputs = representation_inputs(passages, name)

    def passage_vectors(self, texts):
        return self.shared.passage_vectors([self.inputs[text] for text in texts])

    def query_vector(self, query):
        return self.shared.query_vector(query)


def lexical_or(connection, corpus_id, service, query, config):
    """Parameter-bound query; all lexemes come from PostgreSQL's English parser."""
    require_schema(connection)
    with connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        rows = connection.execute('''WITH eligible AS MATERIALIZED (
            SELECT passage_id, metadata, terms FROM ic_rag.passages
            WHERE corpus_id=%s AND current AND (services @> ARRAY[%s]::text[]
                OR services @> ARRAY['global']::text[])),
            lexemes AS (SELECT unnest(tsvector_to_array(to_tsvector('english', %s))) AS term),
            q AS (SELECT to_tsquery('simple', coalesce(string_agg(quote_literal(term), ' | ' ORDER BY term), '')) AS terms
                  FROM lexemes)
            SELECT passage_id, metadata, ts_rank_cd(e.terms, q.terms) AS score
            FROM eligible e CROSS JOIN q WHERE e.terms @@ q.terms
            ORDER BY score DESC, passage_id COLLATE "C" ASC''', (corpus_id, service, query)).fetchall()
        result = bound_sources(corpus_id, rows, config, mode="lexical")
    return {"corpus_id": corpus_id, "mode": "lexical_or", "matched_passages": len(rows), **result}


def prior_control(report):
    record = baseline.read(HERE / "calibration-development.v1.json")
    if sha256(report.read_bytes()).hexdigest() != record["raw_report_sha256"]:
        raise ValueError("Prior baseline report identity mismatch")
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    if rows[-1].get("type") != "summary":
        raise ValueError("Prior baseline is incomplete")
    return {(row["case_id"], row["mode"], row["cutoff"]): row
            for row in rows if row["type"] == "retrieval"}


def compare_control(row, previous):
    key = (row["case_id"], row["mode"], row["cutoff"])
    for field in ("query", "query_metadata", "input_sha256", "service"):
        if row.get(field) != previous[key].get(field):
            raise ValueError("Baseline control query/input changed")
    expected = previous[key]["result"]["retrieved_sources"]
    actual = row["result"]["retrieved_sources"]
    if [s["id"] for s in expected] != [s["id"] for s in actual]:
        raise ValueError("Baseline control ranks changed")
    for left, right in zip(expected, actual, strict=True):
        if left["distance"] is not None and not math.isclose(left["distance"], right["distance"], abs_tol=1e-6, rel_tol=0):
            raise ValueError("Baseline control distances changed")


def score(rows, passages, annotations):
    output = []
    for row in rows:
        annotation = annotations[row["case_id"]]
        if any(label["canary"] in row["query"] for label in annotations.values()):
            raise ValueError("Evaluator label leaked into query")
        ids = [source["id"].split(":", 2)[2] for source in row["result"]["retrieved_sources"]]
        violations = sum(passages[p]["status"] != "current"
            or not ({row["service"], "global"} & set(passages[p]["services"])) for p in ids)
        output.append({"type": "score", "case_id": row["case_id"], "representation": row["representation"],
            "mode": row["mode"], "cutoff": row["cutoff"], "category": annotation["category"],
            "passage_ids": ids, "metrics": baseline.metrics(ids, annotation["relevance"]),
            "scope_version_violations": violations})
    return output


def run(connection, model_directory, previous_report, output):
    if output.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Write reports outside the application repository")
    plan = protocol()
    fixture = baseline.verify_frozen()
    previous = prior_control(previous_report)
    with output.open("x", encoding="utf-8") as stream:
        def write(row):
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
        write({"type": "configuration", "protocol": plan, "protocol_sha256": sha256(PROTOCOL.read_bytes()).hexdigest(),
               "runner_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
               "baseline_helper_sha256": sha256((HERE / "retrieval.py").read_bytes()).hexdigest(),
               "fixture_sha256": fixture, "previous_report_sha256": sha256(previous_report.read_bytes()).hexdigest(),
               "held_out_scored": 0, "provider_calls": 0})
        started = time.perf_counter()
        try:
            backend = LocalEmbedder(model_directory)
            shared = baseline.CachedEmbedder(backend)
            inputs = baseline.development_inputs()
            queries = [(case_id, incident, digest, *construct_query(incident, backend.model.token_count))
                       for case_id, incident, digest in inputs]
            prepare_schema(connection)
            initial = load_corpus(ROOT, artifact_contract(), RetrievalConfig(max_distance=0.4))
            raw, corpus_ids = [], {}
            for name in ("body", "title_context"):
                embedder = RepresentationEmbedder(shared, initial.passages, name)
                corpus_ids[name] = {}
                for cutoff in baseline.CUTOFFS:
                    corpus = load_corpus(ROOT, embedder.artifact, RetrievalConfig(max_distance=cutoff))
                    corpus_ids[name][str(cutoff)] = corpus.corpus_id
                    write({"type": "publication", "representation": name, "cutoff": cutoff,
                           "artifact_sha256": identity(embedder.artifact), **ingest(connection, corpus, embedder)})
                    for case_id, incident, digest, query, query_info in queries:
                        modes = ("vector", "lexical", "lexical_or") if name == "body" and cutoff == baseline.CUTOFFS[0] else ("vector",)
                        for mode in modes:
                            began = time.perf_counter()
                            result = (lexical_or(connection, corpus.corpus_id, incident.service, query, corpus.contract["retrieval"])
                                if mode == "lexical_or" else lookup(connection, corpus.corpus_id, incident.service, query,
                                    mode=mode, embedder=embedder if mode == "vector" else None))
                            row = {"type": "retrieval", "case_id": case_id, "representation": name, "mode": mode,
                                "cutoff": cutoff if mode == "vector" else None, "input_sha256": digest,
                                "service": incident.service, "query": query, "query_metadata": query_info,
                                "elapsed_seconds": time.perf_counter() - began, "result": result}
                            raw.append(row)
                            write(row)
                            if name == "body" and mode != "lexical_or":
                                compare_control(row, previous)
            if len(raw) != 312 or len(shared.passages) != 2 or len(shared.queries) != 12:
                raise ValueError("Execution accounting disagrees with preregistered protocol")
            # Keep annotation loading strictly after search/query/embedding work.
            annotations = {label["id"]: label for label in baseline.read(HERE / "labels.json")["labels"]}
            scored = score(raw, {p["passage_id"]: p for p in initial.passages}, annotations)
            for row in scored:
                write(row)
            lexical = {mode: baseline.summarize([row for row in scored if row["mode"] == mode])
                       for mode in ("lexical", "lexical_or")}
            vectors, selected = {}, {}
            for name in ("body", "title_context"):
                vectors[name] = {str(cutoff): baseline.summarize([row for row in scored
                    if row["mode"] == "vector" and row["representation"] == name and row["cutoff"] == cutoff])
                    for cutoff in baseline.CUTOFFS}
                selected[name] = baseline.select_candidate(vectors[name], lexical["lexical_or"])
            eligible = [(name, cutoff, vectors[name][str(cutoff)]) for name, cutoff in selected.items() if cutoff is not None]
            best = min(eligible, key=lambda entry: (-entry[2]["ndcg_at_3"], -entry[2]["precision_at_3"], entry[1], entry[0] != "body")) if eligible else None
            summary = {"type": "summary", "split": "development", "fixture_sha256": fixture,
                "lexical": lexical, "vectors": vectors, "selected_by_representation": selected,
                "selected_representation": best[0] if best else None, "selected_cutoff": best[1] if best else None,
                "selected_corpus_id": corpus_ids[best[0]][str(best[1])] if best else None,
                "corpus_ids": corpus_ids, "control_matches": 156, "searches": len(raw),
                "document_embedding_batches": len(shared.passages), "distinct_query_embeddings": len(shared.queries),
                "elapsed_seconds": time.perf_counter() - started, "held_out_scored": 0, "provider_calls": 0,
                "runtime_promoted": False, "diagnosis_quality_assessed": False,
                "latency_note": "Repeated embeddings cached; not a latency comparison"}
            write(summary)
            return summary
        except BaseException as error:
            write({"type": "failure", "error_type": type(error).__name__, "elapsed_seconds": time.perf_counter()-started})
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
        print(json.dumps({key: result[key] for key in ("selected_representation", "selected_cutoff", "searches", "provider_calls")}))
        return 0
    except Exception as error:
        print(f"Offline representation comparison failed ({type(error).__name__}); inspect retained evidence.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
