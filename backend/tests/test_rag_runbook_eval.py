"""Runbook expansion preserves scope, exact excerpts and bounded source semantics."""

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

from incidentclue.rag.corpus import RetrievalConfig, load_corpus
from incidentclue.rag.embeddings import artifact_contract

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("runbook_evaluation", ROOT / "evals/rag/runbook.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def passages(document="latency"):
    corpus = load_corpus(ROOT, artifact_contract(), RetrievalConfig(max_distance=0.25))
    chosen = [p for p in corpus.passages if p["document_id"] == document]
    rows = [{"metadata": p, "score": {"symptoms": 0.1, "mechanism": 0.4, "checks": 0.5}[p["section"].casefold()]}
            for p in chosen]
    return corpus, sorted(rows, key=lambda r: r["score"])


def test_symptom_match_returns_exact_sibling_guidance_with_honest_distances():
    corpus, rows = passages()
    saved = deepcopy(rows)
    result = evaluation.expand(corpus.corpus_id, "orders", rows, corpus.contract["retrieval"])
    sources = result["retrieved_sources"]
    assert [s["section"].casefold() for s in sources] == ["mechanism", "checks"]
    assert [s["distance"] for s in sources] == [0.4, 0.5]
    assert result["selected_documents"][0]["seed_distance"] == 0.1
    for source in sources:
        original = next(r["metadata"] for r in rows if r["metadata"]["section"] == source["section"])
        assert all(source[k] == original[k] for k in ("text", "path", "line_start", "line_end", "document_sha256"))
    assert rows == saved


def test_no_seed_in_threshold_produces_no_sources():
    corpus, rows = passages()
    for row in rows:
        row["score"] += 0.3
    result = evaluation.expand(corpus.corpus_id, "orders", rows, corpus.contract["retrieval"])
    assert result["retrieved_sources"] == [] and result["selected_documents"] == []


@pytest.mark.parametrize("change", ["superseded", "wrong-service", "duplicate", "nonfinite"])
def test_expansion_rejects_scope_or_identity_corruption(change):
    corpus, rows = passages()
    if change == "superseded":
        rows[0]["metadata"]["status"] = "superseded"
    elif change == "wrong-service":
        rows[0]["metadata"]["services"] = ["inventory"]
    elif change == "duplicate":
        rows.append(deepcopy(rows[0]))
    else:
        rows[0]["score"] = float("nan")
    with pytest.raises(ValueError, match="Invalid scoped"):
        evaluation.expand(corpus.corpus_id, "orders", rows, corpus.contract["retrieval"])


def test_expansion_obeys_shared_top_three_and_byte_bounds():
    corpus, first = passages("latency")
    _, second = passages("dns")
    rows = sorted(first + second, key=lambda r: (r["score"], r["metadata"]["passage_id"]))
    result = evaluation.expand(corpus.corpus_id, "orders", rows, corpus.contract["retrieval"])
    assert len(result["retrieved_sources"]) == 3
    assert result["bounds_excluded"] == 1
    assert result["text_bytes"] <= 3000 and result["context_bytes"] <= 4000
    assert [s["rank"] for s in result["retrieved_sources"]] == [1, 2, 3]


def test_missing_sibling_fails_instead_of_manufacturing_guidance():
    corpus, rows = passages()
    with pytest.raises(ValueError, match="exactly one"):
        evaluation.expand(corpus.corpus_id, "orders", rows[:1], corpus.contract["retrieval"])


def test_candidate_cannot_bypass_lexical_floor_or_false_match_gate():
    summary = {"assessed": 12, "answerable_cases": 6, "no_guidance_cases": 2,
               "answerable_hits": 6, "no_guidance_false_positives": 0, "scope_version_violations": 0,
               "mrr_at_3": 0.6, "ndcg_at_3": 0.8, "precision_at_3": 0.5}
    assert evaluation.baseline.select_candidate({"0.25": summary}, {"mrr_at_3": 0.683333}) is None
    summary["mrr_at_3"] = 0.9
    summary["no_guidance_false_positives"] = 1
    assert evaluation.baseline.select_candidate({"0.25": summary}, {"mrr_at_3": 0.683333}) is None


def test_wrong_archived_report_rejected_before_work(tmp_path):
    report = tmp_path / "wrong.jsonl"
    report.write_text('{}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="identity mismatch"):
        evaluation.prior_rows(report)
