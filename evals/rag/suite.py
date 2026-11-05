"""Explicit RAG fixture validation and baseline capture; never imported by runtime."""
import argparse
import json
import time
from datetime import date
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from incidentclue.diagnosis import DiagnosisError, validate_support_excerpts
from incidentclue.investigation import investigate_incident
from incidentclue.lifecycle import runtime_fingerprint
from incidentclue.llm import DEFAULT_MODEL, create_groq_client
from incidentclue.schemas import Diagnosis, GenerationDiagnosis, Incident
from incidentclue.tools import HealthSnapshot, LogSnapshot
from openai import APIStatusError
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
SUITE = Path(__file__).resolve().parent
MAX_DIAGNOSTIC_CHARACTERS = 16_384

_DIAGNOSIS_FIELDS = frozenset({
    "status", "probable_cause", "evidence_ids", "missing_evidence", "next_checks",
    "source_ids", "support_assessment", "evidence_links", "blocking_gaps",
    "evidence_id", "excerpt", "relation",
})
_VALIDATION_TYPES = frozenset({
    "bool_type", "datetime_type", "extra_forbidden", "float_type", "int_type",
    "json_invalid", "list_type", "literal_error", "missing", "model_type",
    "none_required", "string_type", "string_too_long", "string_too_short",
    "too_long", "too_short", "union_type", "value_error",
})
_VALIDATION_RULES = {
    "A hypothesis requires a nonblank probable cause.": "hypothesis_requires_cause",
    "A hypothesis requires at least one evidence ID.": "hypothesis_requires_evidence",
    "An insufficient-evidence result must have a null cause.": "abstention_cause_must_be_null",
    "Support links must refer to diagnosis evidence_ids.": "support_link_outside_diagnosis_evidence",
    "Every hypothesis evidence ID requires a support link.": "hypothesis_support_links_must_match",
    "A hypothesis cannot declare blocking gaps.": "hypothesis_has_blocking_gaps",
    "Abstention requires at least one blocking gap.": "abstention_requires_blocking_gap",
    "Duplicate evidence links are not allowed.": "duplicate_evidence_links",
    "Support assessment exceeds the 2,000-byte limit.": "support_assessment_over_byte_limit",
}
_DIAGNOSIS_FAILURE_CATEGORIES = {
    "Expected exactly one tool-selection response.": "malformed_response",
    "Provider returned an invalid tool-selection message.": "malformed_response",
    "Provider did not complete tool selection normally.": "incomplete_response",
    "Provider returned invalid tool calls.": "malformed_response",
    "Inconsistent tool-selection response.": "tool_selection_contract_invalid",
    "Expected exactly one diagnosis response choice.": "malformed_response",
    "Provider returned an invalid diagnosis message.": "malformed_response",
    "Provider did not complete the diagnosis normally.": "incomplete_response",
    "Provider refused the diagnosis request.": "provider_refusal",
    "Provider returned an unsupported tool request.": "unsupported_tool_call",
    "Provider returned no diagnosis content.": "malformed_response",
    "Provider response did not satisfy the diagnosis contract.": "generation_contract_invalid",
    "Provider cited evidence outside the supplied incident or invalid support excerpts.": "reference_integrity_invalid",
}


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
                    "evidence_ids": [], "source_ids": [], "missing_evidence": ["Harness smoke; no quality judgment."],
                    "support_assessment": {"evidence_links": [], "blocking_gaps": ["Additional observations are required; scripted response."]},
                    "next_checks": []})
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                message=ChatCompletionMessage(role="assistant", content=content))])
    return Fake()


def provider_error_details(error):
    """Classify known provider errors without retaining free-text messages/headers."""
    if not isinstance(error, APIStatusError):
        return None
    body = error.body
    details = body.get("error", body) if isinstance(body, dict) else {}
    if not isinstance(details, dict):
        details = {}
    categories = {
        "tool_use_failed": "tool_generation_rejected",
        "json_validate_failed": "structured_output_rejected",
        "context_length_exceeded": "context_limit",
        "rate_limit_exceeded": "rate_limit",
        "invalid_api_key": "authentication",
    }
    code = details.get("code")
    category = categories.get(code, "unclassified") if isinstance(code, str) else "unclassified"
    if error.status_code == 429:
        category = "rate_limit"
    result = {"http_status": error.status_code, "category": category}
    # A numeric server hint can inform a later manual run; never sleep/retry here.
    hint = error.response.headers.get("retry-after", "")
    try:
        seconds = float(hint)
        if 0 <= seconds <= 86400:
            result["retry_after_seconds"] = seconds
    except (ValueError, TypeError):
        pass
    return result


def rejected_generation_details(error, *, diagnosis):
    """Inspect bounded JSON shape; omit values, arbitrary keys and error text."""
    result = {"availability": "absent", "truncation": "unknown"}
    if not isinstance(error, APIStatusError) or not isinstance(error.body, dict):
        return result
    details = error.body.get("error", error.body)
    if not isinstance(details, dict) or "failed_generation" not in details:
        return result
    content = details["failed_generation"]
    if not isinstance(content, str):
        return {**result, "availability": "not_text"}
    result.update(availability="present", character_count=len(content))
    if len(content) > MAX_DIAGNOSTIC_CHARACTERS:
        return {**result, "json_status": "not_inspected_size_limit"}
    try:
        # Non-finite constants are not JSON. No decoder messages/positions escape.
        def reject_constant(value):
            raise ValueError
        parsed = json.loads(content, parse_constant=reject_constant)
    except RecursionError:
        return {**result, "json_status": "not_inspected_parser_limit"}
    except ValueError:
        return {**result, "json_status": "invalid"}

    def shape(value):
        if value is None:
            return "null"
        return {dict: "object", list: "array", str: "string", bool: "boolean",
                int: "number", float: "number"}[type(value)]

    result.update(json_status="valid", top_level_type=shape(parsed))
    if isinstance(parsed, dict):
        # Fixed names only: arbitrary generated keys can themselves contain secrets.
        fields = (tuple(Diagnosis.model_fields) if diagnosis
                  else ("name", "arguments", "tool_calls", "function", "parameters"))
        result["known_field_types"] = {name: shape(parsed[name]) for name in fields if name in parsed}
        result["unknown_field_count"] = sum(name not in fields for name in parsed)
    if diagnosis:
        result["generation_contract_version"] = 2
        for name, contract in (("legacy_diagnosis_contract_valid", Diagnosis),
                               ("generation_diagnosis_contract_valid", GenerationDiagnosis)):
            try:
                contract.model_validate_json(content)
                result[name] = True
            except ValidationError as validation_error:
                result[name] = False
                if name == "generation_diagnosis_contract_valid":
                    result["generation_contract_issues"] = _safe_validation_issues(validation_error)
    return result


def _safe_validation_issues(error, *, limit=3):
    """Return bounded rule codes and schema paths, never inputs or error messages."""
    try:
        issues = error.errors(include_input=False, include_url=False)
    except (AttributeError, TypeError, ValueError):
        return [{"path": "$", "type": "validation_error"}]

    if not isinstance(issues, list):
        return [{"path": "$", "type": "validation_error"}]
    result = []
    for issue in issues[:limit]:
        if not isinstance(issue, dict):
            continue
        location = issue.get("loc", ())
        path = []
        if isinstance(location, (list, tuple)):
            for part in location[:8]:
                if type(part) is int:
                    path.append("[]")
                elif type(part) is str and part in _DIAGNOSIS_FIELDS:
                    path.append(part)
                else:
                    path.append("other")
        code = issue.get("type")
        safe_type = code if isinstance(code, str) and code in _VALIDATION_TYPES else "validation_error"
        item = {"path": ".".join(path) if path else "$", "type": safe_type}
        context = issue.get("ctx")
        cause = context.get("error") if isinstance(context, dict) else None
        if isinstance(cause, ValueError):
            message = cause.args[0] if len(cause.args) == 1 and type(cause.args[0]) is str else None
            rule = _VALIDATION_RULES.get(message)
            if rule is not None:
                item["rule"] = rule
        result.append(item)
    return result or [{"path": "$", "type": "validation_error"}]


def failure_diagnostic(error, *, phase=None):
    """Classify a failed evaluation using fixed labels and bounded local rule details."""
    phase = phase if phase in {"selection", "diagnosis"} else "unknown"
    provider = provider_error_details(error)
    if provider is not None:
        return {"origin": "provider", "phase": phase, "category": provider["category"]}

    if isinstance(error, DiagnosisError):
        message = error.args[0] if len(error.args) == 1 and type(error.args[0]) is str else None
        category = _DIAGNOSIS_FAILURE_CATEGORIES.get(message, "local_validation_failure")
        result = {"origin": "local_validation", "phase": phase, "category": category}
        cause = error.__cause__
        if category == "generation_contract_invalid" and isinstance(cause, ValidationError):
            result["issues"] = _safe_validation_issues(cause)
        return result

    if isinstance(error, ValidationError):
        return {"origin": "local_validation", "phase": phase,
                "category": "validation_error", "issues": _safe_validation_issues(error)}
    return {"origin": "execution", "phase": phase, "category": "unknown"}


def recorded_generation_contract_valid(record):
    """Check original returned diagnosis content, never a defaulted saved result.

    None means no uniquely identified current-contract generation is available. Provider
    exceptions remain execution failures regardless of rejected-content metadata.
    """
    attempts = [a for a in record.get("attempts", [])
                if a.get("phase") == "diagnosis" and a.get("status") == "response_received"]
    if len(attempts) != 1:
        return None
    index = attempts[0].get("response_index")
    attempt_number = attempts[0].get("attempt")
    if type(attempt_number) is not int:
        return None
    request_index = attempt_number - 1
    requests = record.get("requests", [])
    if not 0 <= request_index < len(requests):
        return None
    required = requests[request_index].get("response_format", {}).get("json_schema", {}).get("schema", {}).get("required", [])
    if "support_assessment" not in required:
        return None  # An older request did not ask for the current support contract.
    responses = record.get("responses", [])
    if type(index) is not int or not 0 <= index < len(responses):
        return None
    choices = responses[index]
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        return None
    try:
        GenerationDiagnosis.model_validate_json(content)
    except ValidationError:
        return False
    return True


def saved_support_checks(incident, saved):
    """Separate declared consistency and exact-quote provenance from semantic truth."""
    result = {"support_record_available": saved.get("support_assessment") is not None,
              "support_internal_consistency_valid": None,
              "support_excerpt_membership_valid": None, "semantic_support_verified": None}
    if not result["support_record_available"]:
        return result
    try:
        diagnosis = Diagnosis.model_validate(saved)
        GenerationDiagnosis.model_validate(diagnosis.model_dump())
        result["support_internal_consistency_valid"] = True
    except ValidationError:
        return {**result, "support_internal_consistency_valid": False}
    try:
        validate_support_excerpts(incident, diagnosis)
        result["support_excerpt_membership_valid"] = True
    except ValueError:
        result["support_excerpt_membership_valid"] = False
    return result


class RecordingClient:
    """Keep capture across SDK with_options copies without changing the SDK object."""
    def __init__(self, client, requests, responses, usage, attempts):
        self.client, self.requests, self.responses, self.usage = client, requests, responses, usage
        self.attempts = attempts
        self.chat = SimpleNamespace(completions=self)

    def with_options(self, **options):
        return RecordingClient(self.client.with_options(**options), self.requests, self.responses,
                               self.usage, self.attempts)

    def create(self, **request):
        self.requests.append(request)
        attempt = {"attempt": len(self.requests), "phase": "diagnosis" if "response_format" in request
                   else "selection", "status": "started", "usage": None}
        self.attempts.append(attempt)
        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(**request)
        except Exception as error:
            attempt.update(status="exception", error_type=type(error).__name__,
                           elapsed_seconds=time.perf_counter() - started)
            details = provider_error_details(error)
            if details is not None:
                attempt["provider_error"] = details
            attempt["rejected_generation"] = rejected_generation_details(
                error, diagnosis=attempt["phase"] == "diagnosis")
            raise
        attempt.update(status="response_received", response_index=len(self.responses),
                       elapsed_seconds=time.perf_counter() - started)
        choices = response.choices
        self.responses.append([choice.model_dump(mode="json") if hasattr(choice, "model_dump")
            else {"finish_reason": choice.finish_reason,
                  "message": choice.message.model_dump(mode="json")}
            for choice in choices] if isinstance(choices, list) else choices)
        observed = getattr(response, "usage", None)
        self.usage.append(observed.model_dump(mode="json") if observed is not None else None)
        attempt["usage"] = self.usage[-1]
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
            requests, usage, responses, attempts = [], [], [], []
            recording = RecordingClient(client, requests, responses, usage, attempts)
            rate_limited = False
            try:
                result = investigate_incident(Incident.model_validate(payload["incident"]), recording,
                    HealthSnapshot.model_validate(payload["health"]), model,
                    logs=LogSnapshot.model_validate(payload["logs"]) if payload["logs"] else None,
                    events=events, steps=steps, summary=summary)
                record.update({"status": "ok", "diagnosis": result.model_dump(mode="json")})
            except Exception as error:  # noqa: BLE001 - capture all evaluation failures
                # Keep private provider/credential detail out of captured evidence.
                record.update({"status": "error", "error_type": type(error).__name__})
                phase = attempts[-1].get("phase") if attempts else None
                record["failure_diagnostic"] = failure_diagnostic(error, phase=phase)
                details = provider_error_details(error)
                if details is not None:
                    record["provider_error"] = details
                if type(error) is DiagnosisError:
                    record["validation_error"] = str(error)
                record["cause_types"] = []
                cause = error.__cause__
                while cause is not None and len(record["cause_types"]) < 5:
                    record["cause_types"].append(type(cause).__name__)
                    cause = cause.__cause__
                rate_limited = (details is not None and details["category"] == "rate_limit") or type(error).__name__ == "RateLimitError"
            record.update({"elapsed_seconds": time.perf_counter() - started,
                           "events": events, "steps": steps, "summary": summary,
                           "model_attempts": len(requests), "provider_usage": usage,
                           "attempts": attempts,
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
