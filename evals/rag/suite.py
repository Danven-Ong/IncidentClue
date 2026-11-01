"""Explicit RAG fixture validation and baseline capture; never imported by runtime."""
import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import time
from types import SimpleNamespace

from incidentclue.investigation import investigate_incident
from incidentclue.diagnosis import DiagnosisError
from incidentclue.lifecycle import runtime_fingerprint
from incidentclue.llm import DEFAULT_MODEL, create_groq_client
from incidentclue.schemas import Incident
from incidentclue.tools import HealthSnapshot, LogSnapshot

ROOT = Path(__file__).resolve().parents[2]
SUITE = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def chunks(document, root=ROOT):
    """Heading sections retain exact source line ranges, without silent truncation."""
    path = root / document["path"]
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    output = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        heading = lines[start][3:].strip()
        output.append({"id": f"{document['id']}#{heading.lower()}",
                       "section": heading, "line_start": start + 1, "line_end": end,
                       "text": "".join(lines[start:end]).rstrip()})
    return output


def validate(root=ROOT, suite=SUITE):
    manifest = load(root / "data/runbooks/manifest.json")
    documents = manifest["documents"]
    assert manifest["schema_version"] == 1 and manifest["synthetic"] is True
    assert manifest["chunk_policy"] == "markdown-h2-v1"
    assert 0 < len(documents) <= 12
    seen, passage_ids = set(), set()
    total = 0
    for doc in documents:
        assert doc["id"] not in seen
        seen.add(doc["id"])
        path = Path(doc["path"])
        assert path.as_posix() == f"data/runbooks/{doc['id']}.md"
        assert (root / path).resolve().is_relative_to((root / "data/runbooks").resolve())
        assert not (root / path).is_symlink()
        assert digest(root / path) == doc["sha256"]
        assert doc["services"] and doc["status"] in ("current", "superseded")
        date.fromisoformat(doc["effective_date"])
        passages = chunks(doc, root)
        assert passages
        for passage in passages:
            assert 0 < len(passage["text"].encode()) <= 1000
            assert passage["id"] not in passage_ids
            passage_ids.add(passage["id"])
        total += len(passages)
    assert total <= 48
    assert all(doc["superseded_by"] in seen for doc in documents if doc["status"] == "superseded")
    cases = load(suite / "cases.json")["cases"]
    labels = load(suite / "labels.json")["labels"]
    assert len(cases) == len(labels) == 24
    assert {c["id"] for c in cases} == {x["id"] for x in labels}
    assert len({c["id"] for c in cases}) == len(cases)
    assert len({x["family"] for x in labels}) == len(labels)
    by_id = {x["id"]: x for x in labels}
    model_visible = []
    for case in cases:
        assert case["split"] in ("development", "held_out")
        assert case["path"] == f"inputs/{case['id']}.json"
        payload = load(suite / case["path"])
        assert set(payload) == {"incident", "health", "logs"}
        incident = Incident.model_validate(payload["incident"])
        health = HealthSnapshot.model_validate(payload["health"])
        assert incident.incident_id == case["id"] and incident.service == health.service
        if payload["logs"] is not None:
            LogSnapshot.model_validate(payload["logs"])
        model_visible.append(json.dumps(payload))
        assert set(by_id[case["id"]]["relevance"]).issubset(passage_ids)
        assert set(by_id[case["id"]]["relevance"].values()).issubset({1, 2})
    for split in ("development", "held_out"):
        categories = [by_id[c["id"]]["category"] for c in cases if c["split"] == split]
        assert len(categories) == 12
        assert {key: categories.count(key) for key in set(categories)} == {
            "answerable": 6, "ambiguous": 2, "insufficient": 2, "no_guidance": 2}
    corpus_text = "\n".join((root / d["path"]).read_text(encoding="utf-8") for d in documents)
    for label in labels:
        assert label["canary"] not in corpus_text
        assert all(label["canary"] not in text for text in model_visible)
        assert label["id"] not in corpus_text
    files = [root / "data/runbooks/manifest.json", suite / "cases.json", suite / "labels.json",
             suite / "rubric.md", *[root / d["path"] for d in documents],
             *[suite / c["path"] for c in cases]]
    hashes = {p.relative_to(root).as_posix(): digest(p) for p in files}
    frozen = sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    lock = suite / "frozen.json"
    if lock.exists():
        assert load(lock)["fixture_sha256"] == frozen, "Frozen evaluation fixtures changed"
    return {"documents": len(documents), "chunks": total, "cases": len(cases),
            "fixture_sha256": frozen, "files": hashes,
            "token_limit_checked": False}


def fake_client():
    """SDK-shaped local smoke only; no labels, semantic scoring or network client."""
    from openai.types.chat import ChatCompletionMessage

    class Fake:
        def __init__(self):
            self.prompts = []
            self.chat = SimpleNamespace(completions=self)

        def with_options(self, **options):
            return self

        def create(self, **request):
            self.prompts.append(request)
            content = "No further lookup requested."
            if "response_format" in request:
                content = json.dumps({"status": "insufficient_evidence", "probable_cause": None,
                    "evidence_ids": [], "missing_evidence": ["Harness smoke; no quality judgment."],
                    "next_checks": []})
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                message=ChatCompletionMessage(role="assistant", content=content))])
    return Fake()


class RecordingClient:
    """Keep capture across SDK with_options copies without changing the SDK object."""
    def __init__(self, client, requests, responses, usage):
        self.client, self.requests, self.responses, self.usage = client, requests, responses, usage
        self.chat = SimpleNamespace(completions=self)

    def with_options(self, **options):
        return RecordingClient(self.client.with_options(**options), self.requests, self.responses, self.usage)

    def create(self, **request):
        self.requests.append(request)
        response = self.client.chat.completions.create(**request)
        self.responses.append([choice.model_dump(mode="json") if hasattr(choice, "model_dump")
            else {"finish_reason": choice.finish_reason,
                  "message": choice.message.model_dump(mode="json")}
            for choice in response.choices])
        observed = getattr(response, "usage", None)
        self.usage.append(observed.model_dump(mode="json") if observed is not None else None)
        return response


def baseline(*, live, model, split, limit, output, env_file=None, case_ids=None):
    frozen = validate()
    cases = [case for case in load(SUITE / "cases.json")["cases"] if case["split"] == split]
    if case_ids is not None:
        if len(case_ids) != len(set(case_ids)) or not set(case_ids).issubset({c["id"] for c in cases}):
            raise ValueError("Case IDs must be unique and belong to the selected split")
        cases = [case for case in cases if case["id"] in case_ids]
    cases = cases[:limit]
    # Only the explicit live branch can read credentials or construct a network client.
    if live:
        import os
        if env_file:
            from dotenv import dotenv_values
            key = dotenv_values(env_file).get("GROQ_API_KEY", "")
        else:
            key = os.environ.get("GROQ_API_KEY", "")
        client = create_groq_client(key or "")
    else:
        client = fake_client()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids overwriting evidence from a previous evaluation.
    with output.open("x", encoding="utf-8") as stream:
        header = {"type": "configuration", "mode": "live" if live else "fake_smoke",
                  "model": model, "split": split, "case_count": len(cases),
                  "fixture_sha256": frozen["fixture_sha256"], "runtime": runtime_fingerprint(),
                  "suite_sha256": digest(Path(__file__)), "quality_evaluated": False}
        stream.write(json.dumps(header) + "\n")
        stream.flush()
        for case in cases:
            payload = load(SUITE / case["path"])
            events, steps, summary = [], [], {}
            started = time.perf_counter()
            record = {"id": case["id"], "input_sha256": digest(SUITE / case["path"])}
            requests, usage, responses = [], [], []
            recording = RecordingClient(client, requests, responses, usage)
            rate_limited = False
            try:
                result = investigate_incident(Incident.model_validate(payload["incident"]), recording,
                    HealthSnapshot.model_validate(payload["health"]), model,
                    logs=LogSnapshot.model_validate(payload["logs"]) if payload["logs"] else None,
                    events=events, steps=steps, summary=summary)
                record.update({"status": "ok", "diagnosis": result.model_dump(mode="json")})
            except Exception as error:
                # Keep private provider/credential detail out of captured evidence.
                record.update({"status": "error", "error_type": type(error).__name__})
                if type(error) is DiagnosisError:
                    record["validation_error"] = str(error)
                record["cause_types"] = []
                cause = error.__cause__
                while cause is not None and len(record["cause_types"]) < 5:
                    record["cause_types"].append(type(cause).__name__)
                    cause = cause.__cause__
                rate_limited = type(error).__name__ == "RateLimitError"
            record.update({"elapsed_seconds": time.perf_counter() - started,
                           "events": events, "steps": steps, "summary": summary,
                           "model_attempts": len(requests), "provider_usage": usage,
                           "requests": requests, "responses": responses})
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            if rate_limited:
                stream.write(json.dumps({"type": "stopped", "reason": "provider_rate_limit",
                    "remaining_ids": [item["id"] for item in cases[cases.index(case) + 1:]]}) + "\n")
                break
    return {"output": str(output), "cases": len(cases), "mode": header["mode"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "baseline"])
    parser.add_argument("--live", action="store_true", help="Explicitly permit provider calls")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--split", choices=["development", "held_out"], default="development")
    parser.add_argument("--limit", type=int, choices=range(1, 13), default=12)
    parser.add_argument("--case-ids", nargs="+", help="Select named cases within the selected split")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "validate":
        print(json.dumps(validate(), indent=2))
    else:
        if args.output is None:
            parser.error("baseline requires --output")
        print(json.dumps(baseline(live=args.live, model=args.model, split=args.split,
            limit=args.limit, output=args.output, env_file=args.env_file, case_ids=args.case_ids)))


if __name__ == "__main__":
    main()
