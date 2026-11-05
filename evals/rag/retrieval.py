"""Development-only real retrieval evaluation. No generation client or live mode."""

import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import socket
import time

import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict

from incidentclue.rag.context import QUERY_POLICY, construct_query
from incidentclue.rag.corpus import RetrievalConfig, identity, load_corpus
from incidentclue.rag.embeddings import LocalEmbedder, artifact_contract
from incidentclue.rag.storage import ingest, lookup, prepare_schema
from incidentclue.schemas import Incident

ROOT = Path(__file__).resolve().parents[2]
SUITE = Path(__file__).resolve().parent
CUTOFFS = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 2.0)
SELECTION_POLICY = {
    "version": "development-retrieval-v1",
    "minimum_answerable_hits": 5,
    "maximum_no_guidance_false_positives": 0,
    "maximum_scope_version_violations": 0,
    "minimum_mrr": "lexical_nonempty_relevance_mean",
    "eligible_cutoffs": list(CUTOFFS[:-1]),
    "tie_break": ["highest_ndcg", "highest_precision_at_3", "lowest_cutoff"],
    "diagnostic_only_cutoff": 2.0,
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_frozen(root=ROOT):
    """Hash held-out bytes for integrity, without interpreting or scoring their inputs."""
    frozen = read(root / "evals/rag/frozen.json")
    hashes = {}
    for relative, expected in frozen["files"].items():
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Frozen file escapes repository root")
        digest = sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError("Frozen fixtures changed")
        hashes[relative] = digest
    if sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest() != frozen["fixture_sha256"]:
        raise ValueError("Frozen fixture identity mismatch")
    return frozen["fixture_sha256"]


def development_inputs(root=ROOT):
    cases = read(root / "evals/rag/cases.json")["cases"]
    chosen = [case for case in cases if case["split"] == "development"]
    if len(chosen) != 12 or len({case["id"] for case in chosen}) != 12:
        raise ValueError("Expected exactly twelve unique development cases")
    output = []
    for case in chosen:
        if case["path"] != f"inputs/{case['id']}.json":
            raise ValueError("Unexpected development input path")
        path = root / "evals/rag" / case["path"]
        payload = read(path)
        # Same observations available at creation; do not inject unfetched snapshots.
        incident = Incident.model_validate(payload["incident"])
        output.append((case["id"], incident, sha256(path.read_bytes()).hexdigest()))
    return output


def metrics(passage_ids, relevance):
    if len(passage_ids) > 3 or len(set(passage_ids)) != len(passage_ids):
        raise ValueError("Metrics require at most three unique passages")
    grades = [relevance.get(identifier, 0) for identifier in passage_ids]
    hits = sum(grade > 0 for grade in grades)
    result = {"returned": len(passage_ids), "relevant_returned": hits,
              "precision_at_3": hits / 3, "recall_at_3": None, "hit": None,
              "mrr_at_3": None, "ndcg_at_3": None}
    if relevance:
        dcg = sum((2 ** grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))
        ideal = sum((2 ** grade - 1) / math.log2(rank + 2)
                    for rank, grade in enumerate(sorted(relevance.values(), reverse=True)[:3]))
        result.update(recall_at_3=hits / len(relevance), hit=hits > 0,
            mrr_at_3=next((1 / (rank + 1) for rank, grade in enumerate(grades) if grade > 0), 0.0),
            ndcg_at_3=dcg / ideal)
    return result


def summarize(rows):
    assessed = [row for row in rows if row["metrics"]["recall_at_3"] is not None]
    answerable = [row for row in rows if row["category"] == "answerable"]
    no_guidance = [row for row in rows if row["category"] == "no_guidance"]
    def mean(key, selected):
        return sum(row["metrics"][key] for row in selected) / len(selected) if selected else None
    return {"planned": 12, "assessed": len(rows), "nonempty_relevance_cases": len(assessed),
        "recall_at_3": mean("recall_at_3", assessed), "mrr_at_3": mean("mrr_at_3", assessed),
        "ndcg_at_3": mean("ndcg_at_3", assessed), "precision_at_3": mean("precision_at_3", rows),
        "hit_cases": sum(row["metrics"]["hit"] is True for row in assessed),
        "answerable_cases": len(answerable),
        "answerable_hits": sum(row["metrics"]["hit"] is True for row in answerable),
        "no_guidance_cases": len(no_guidance),
        "no_guidance_false_positives": sum(row["metrics"]["returned"] > 0 for row in no_guidance),
        "empty_results": sum(row["metrics"]["returned"] == 0 for row in rows),
        "scope_version_violations": sum(row["scope_version_violations"] for row in rows)}


def select_candidate(vector_summaries, lexical):
    eligible = []
    for cutoff, summary in vector_summaries.items():
        if (float(cutoff) in SELECTION_POLICY["eligible_cutoffs"] and summary["assessed"] == 12
                and summary["answerable_cases"] == 6 and summary["no_guidance_cases"] == 2
                and summary["answerable_hits"] >= 5 and summary["no_guidance_false_positives"] == 0
                and summary["scope_version_violations"] == 0
                and summary["mrr_at_3"] >= lexical["mrr_at_3"]):
            eligible.append((float(cutoff), summary))
    if not eligible:
        return None
    return min(eligible, key=lambda item: (-item[1]["ndcg_at_3"],
                                          -item[1]["precision_at_3"], item[0]))[0]


class CachedEmbedder:
    """Cache only repeat embeddings, not retrieval results; preserve actual APIs."""
    def __init__(self, backend):
        self.backend = backend
        self.artifact = backend.artifact
        self.passages = {}
        self.queries = {}

    def passage_vectors(self, texts):
        key = tuple(texts)
        if key not in self.passages:
            self.passages[key] = self.backend.passage_vectors(texts)
        return self.passages[key]

    def query_vector(self, query):
        if query not in self.queries:
            self.queries[query] = self.backend.query_vector(query)
        return self.queries[query]


def run(connection, model_directory, output, *, root=ROOT):
    """Output is outside application Git and exclusive; preserve errors/partial rows."""
    if output.resolve().is_relative_to(root.resolve()):
        raise ValueError("Evaluation output must be outside the application repository")
    fixture = verify_frozen(root)
    # Reserve the output before any expensive work. Never replace a previous report.
    with output.open("x", encoding="utf-8") as stream:
        def write(row):
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
        write({"type": "configuration", "split": "development", "fixture_sha256": fixture,
               "cutoffs": CUTOFFS, "selection_policy": SELECTION_POLICY, "query_policy": QUERY_POLICY,
               "provider_calls": 0, "held_out_scored": 0,
               "scoring": "frozen assistant-curated synthetic relevance annotations; not independent truth",
               "runner_sha256": sha256(Path(__file__).read_bytes()).hexdigest()})
        started = time.perf_counter()
        try:
            backend = LocalEmbedder(model_directory)
            embedder = CachedEmbedder(backend)
            inputs = development_inputs(root)
            queries = [(case_id, incident, digest, *construct_query(incident, backend.model.token_count))
                       for case_id, incident, digest in inputs]
            prepare_schema(connection)  # Explicit isolated evaluation setup, never application startup.
            raw = []
            corpora = {}
            for cutoff in CUTOFFS:
                corpus = load_corpus(root, artifact_contract(), RetrievalConfig(max_distance=cutoff))
                corpora[str(cutoff)] = corpus.corpus_id
                publication = ingest(connection, corpus, embedder)
                write({"type": "publication", "cutoff": cutoff, **publication})
                for case_id, incident, digest, query, query_info in queries:
                    modes = ("vector", "lexical") if cutoff == CUTOFFS[0] else ("vector",)
                    for mode in modes:
                        began = time.perf_counter()
                        result = lookup(connection, corpus.corpus_id, incident.service, query,
                                        mode=mode, embedder=embedder if mode == "vector" else None)
                        row = {"type": "retrieval", "case_id": case_id, "input_sha256": digest,
                            "mode": mode, "cutoff": cutoff if mode == "vector" else None,
                            "service": incident.service, "query": query, "query_metadata": query_info,
                            "elapsed_seconds": time.perf_counter() - began, "result": result}
                        raw.append(row)
                        write(row)
            # Annotation loading/scoring happens after all query/ingestion/retrieval work.
            annotations = {label["id"]: label for label in read(root / "evals/rag/labels.json")["labels"]}
            corpus = load_corpus(root, artifact_contract(), RetrievalConfig(max_distance=CUTOFFS[0]))
            passages = {p["passage_id"]: p for p in corpus.passages}
            scored = []
            for row in raw:
                annotation = annotations[row["case_id"]]
                if annotation["canary"] in row["query"] or row["case_id"] in row["query"]:
                    raise ValueError("Evaluator metadata leaked into a query")
                ids = [source["id"].split(":", 2)[2] for source in row["result"]["retrieved_sources"]]
                violations = sum(passages[p]["status"] != "current"
                    or not ({row["service"], "global"} & set(passages[p]["services"])) for p in ids)
                record = {"type": "score", "case_id": row["case_id"], "mode": row["mode"],
                    "cutoff": row["cutoff"], "category": annotation["category"],
                    "passage_ids": ids, "metrics": metrics(ids, annotation["relevance"]),
                    "scope_version_violations": violations}
                scored.append(record)
                write(record)
            lexical = summarize([row for row in scored if row["mode"] == "lexical"])
            vectors = {str(cutoff): summarize([row for row in scored
                if row["mode"] == "vector" and row["cutoff"] == cutoff]) for cutoff in CUTOFFS}
            selected = select_candidate(vectors, lexical)
            summary = {"type": "summary", "split": "development", "fixture_sha256": fixture,
                "lexical": lexical, "vectors": vectors, "selected_cutoff": selected,
                "selected_corpus_id": corpora[str(selected)] if selected is not None else None,
                "selection_policy": SELECTION_POLICY, "query_policy": QUERY_POLICY,
                "corpus_ids": corpora, "artifact_sha256": identity(artifact_contract()),
                "document_embedding_batches": len(embedder.passages),
                "distinct_query_embeddings": len(embedder.queries), "searches": len(raw),
                "elapsed_seconds": time.perf_counter() - started, "provider_calls": 0,
                "held_out_scored": 0, "diagnosis_quality_assessed": False,
                "latency_note": "Includes cached repeat embeddings; not a latency comparison"}
            write(summary)
            return summary
        except BaseException as error:
            write({"type": "failure", "error_type": type(error).__name__,
                   "elapsed_seconds": time.perf_counter() - started})
            raise


def block_external_sockets():
    """Allow only loopback PostgreSQL; fail closed on provider/download attempts."""
    original = socket.socket.connect
    original_resolve = socket.getaddrinfo
    original_create = socket.create_connection
    def permitted(host):
        if host not in ("127.0.0.1", "::1"):
            raise RuntimeError("External network disabled for retrieval evaluation")
    def guarded(sock, address):
        if not isinstance(address, tuple):
            raise RuntimeError("External network disabled for retrieval evaluation")
        permitted(address[0])
        return original(sock, address)
    def resolve(host, *args, **kwargs):
        permitted(host)
        return original_resolve(host, *args, **kwargs)
    def create(address, *args, **kwargs):
        permitted(address[0])
        return original_create(address, *args, **kwargs)
    socket.socket.connect = guarded
    socket.getaddrinfo = resolve
    socket.create_connection = create


def connection_parameters(url):
    parameters = conninfo_to_dict(url)
    if (parameters.get("host") not in ("127.0.0.1", "::1")
            or parameters.get("hostaddr", parameters["host"]) not in ("127.0.0.1", "::1")
            or parameters.get("service") or not parameters.get("dbname")
            or not parameters.get("port", "").isdigit()):
        raise ValueError("Evaluation requires an explicit loopback test database")
    # Explicit address also defeats ambient PGHOSTADDR/service configuration.
    parameters.update(hostaddr=parameters["host"], connect_timeout="5", sslmode="disable")
    return parameters


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    block_external_sockets()
    try:
        parameters = connection_parameters(os.environ.get("INCIDENTCLUE_RAG_TEST_DATABASE_URL", ""))
        with psycopg.connect(**parameters, autocommit=True, row_factory=dict_row) as connection:
            # libpq may use native sockets; also enforce an explicit loopback database.
            if connection.info.host not in ("127.0.0.1", "::1"):
                raise ValueError("Evaluation requires an explicitly configured loopback test database")
            result = run(connection, args.model_dir, args.output)
        print(json.dumps({"selected_cutoff": result["selected_cutoff"],
                          "searches": result["searches"], "provider_calls": 0}))
        return 0
    except Exception as error:
        print(f"Offline retrieval evaluation failed ({type(error).__name__}); inspect the retained report.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
