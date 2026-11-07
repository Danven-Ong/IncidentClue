"""Support provenance and consistency are enforceable; causal truth is not."""

from copy import deepcopy
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import ValidationError
import pytest

from incidentclue.diagnosis import (
    DiagnosisError,
    _validate_diagnosis,
    validate_saved_diagnosis,
)
from incidentclue.investigation import initial_investigation_state
from incidentclue.lifecycle import SavedState
from incidentclue.persistence import json_data, restore_update
from incidentclue.schemas import (
    Diagnosis,
    GenerationDiagnosis,
    Incident,
    SupportAssessment,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "filename", ["paired_remaining.py"]
)
def test_historical_live_protocols_cannot_authorize_new_support_format(
    filename, tmp_path, monkeypatch
):
    spec = importlib.util.spec_from_file_location(
        "retired_paired", ROOT / "evals/rag" / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module.psycopg, "connect", lambda **kw: pytest.fail("Database accessed")
    )
    monkeypatch.setattr(
        module, "create_groq_client", lambda *a: pytest.fail("Provider constructed")
    )
    approval = module.sha256(module.PROTOCOL.read_bytes()).hexdigest()
    output = tmp_path / "not-created.jsonl"
    with pytest.raises(ValueError, match="Historical paired protocol"):
        module.run(None, tmp_path, output, live=True, approval=approval)
    with pytest.raises(SystemExit) as error:
        module.main(
            [
                "--live",
                "--approve-protocol",
                approval,
                "--model-dir",
                str(tmp_path),
                "--output",
                str(output),
            ]
        )
    assert error.value.code == 2 and not output.exists()


def example(case, status):
    incident = Incident.model_validate(
        json.loads((ROOT / f"evals/rag/inputs/{case}.json").read_text())["incident"]
    )
    observation = incident.evidence[0]
    data = {
        "status": status,
        "probable_cause": "A bounded observed mechanism."
        if status == "hypothesis"
        else None,
        "evidence_ids": [observation.id],
        "source_ids": [],
        "missing_evidence": [],
        "next_checks": [],
        "support_assessment": {
            "evidence_links": [
                {
                    "evidence_id": observation.id,
                    "excerpt": observation.content,
                    "relation": "The observation records the proposed mechanism.",
                }
            ],
            "blocking_gaps": []
            if status == "hypothesis"
            else ["The incident-level mechanism is not established."],
        },
    }
    return incident, data


@pytest.mark.parametrize(
    "case,status",
    [
        ("case-01", "hypothesis"),
        ("case-11", "hypothesis"),
        ("case-08", "insufficient_evidence"),
        ("case-12", "insufficient_evidence"),
    ],
)
def test_support_survives_generation_json_checkpoint_and_saved_state(case, status):
    incident, data = example(case, status)
    original = deepcopy(data)
    diagnosis = _validate_diagnosis(incident, json.dumps(data))
    assert type(diagnosis) is Diagnosis and diagnosis.model_dump(mode="json") == data
    assert data == original
    assert restore_update(json_data({"diagnosis": diagnosis}))["diagnosis"] == diagnosis
    serde = JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=[],
        allowed_msgpack_modules=[
            ("incidentclue.schemas", name)
            for name in ("Incident", "Evidence", "Diagnosis")
        ],
    )
    restored = serde.loads_typed(serde.dumps_typed({"diagnosis": diagnosis}))
    assert restored["diagnosis"] == diagnosis
    state = {
        **initial_investigation_state(incident),
        "diagnosis": restored["diagnosis"],
    }
    assert SavedState.model_validate(state).diagnosis == diagnosis
    assert validate_saved_diagnosis(incident, diagnosis.model_dump_json()) == diagnosis


@pytest.mark.parametrize(
    "change",
    [
        "missing_support",
        "null_support",
        "missing_links",
        "missing_gaps",
        "no_links",
        "uncovered_citation",
        "outside_citations",
        "foreign_id",
        "source_as_evidence",
        "invented_excerpt",
        "wrong_observation",
        "blocking_hypothesis",
        "duplicate",
        "blank_excerpt",
        "blank_relation",
        "blank_gap",
        "long_excerpt",
        "long_relation",
        "long_gap",
        "many_links",
        "many_gaps",
    ],
)
def test_invalid_new_support_fails_without_repair(change):
    incident, data = example("case-01", "hypothesis")
    support = data["support_assessment"]
    link = support["evidence_links"][0]
    if change == "missing_support":
        data.pop("support_assessment")
    elif change == "null_support":
        data["support_assessment"] = None
    elif change == "missing_links":
        support.pop("evidence_links")
    elif change == "missing_gaps":
        support.pop("blocking_gaps")
    elif change == "no_links":
        support["evidence_links"] = []
    elif change == "uncovered_citation":
        data["evidence_ids"].append("other")
    elif change == "outside_citations":
        link["evidence_id"] = "other"
    elif change in ("foreign_id", "source_as_evidence"):
        identifier = "foreign" if change == "foreign_id" else "source:runbook:mechanism"
        data["evidence_ids"] = [identifier]
        link["evidence_id"] = identifier
    elif change == "invented_excerpt":
        link["excerpt"] = "Invented observation"
    elif change == "wrong_observation":
        other = incident.evidence[0].model_copy(
            update={"id": "other", "content": "Different observation"}
        )
        incident = incident.model_copy(update={"evidence": [*incident.evidence, other]})
        data["evidence_ids"] = ["other"]
        link["evidence_id"] = "other"
    elif change == "blocking_hypothesis":
        support["blocking_gaps"] = ["Correlation is unresolved."]
    elif change == "duplicate":
        support["evidence_links"].append(deepcopy(link))
    elif change == "blank_excerpt":
        link["excerpt"] = " "
    elif change == "blank_relation":
        link["relation"] = "\t"
    elif change == "blank_gap":
        support["blocking_gaps"] = [" "]
    elif change == "long_excerpt":
        link["excerpt"] = "x" * 241
    elif change == "long_relation":
        link["relation"] = "x" * 241
    elif change == "long_gap":
        support["blocking_gaps"] = ["x" * 241]
    elif change == "many_links":
        support["evidence_links"] = [deepcopy(link)] * 4
    elif change == "many_gaps":
        support["blocking_gaps"] = ["gap"] * 4
    before = deepcopy(data)
    with pytest.raises(DiagnosisError):
        _validate_diagnosis(incident, json.dumps(data))
    assert data == before


def test_abstention_requires_a_declared_gap_but_not_support_links():
    incident, data = example("case-12", "insufficient_evidence")
    data["support_assessment"]["evidence_links"] = []
    assert (
        _validate_diagnosis(incident, json.dumps(data)).status
        == "insufficient_evidence"
    )
    data["support_assessment"]["blocking_gaps"] = []
    with pytest.raises(DiagnosisError):
        _validate_diagnosis(incident, json.dumps(data))


def test_support_total_limit_counts_utf8_bytes_without_truncation():
    value = {
        "evidence_links": [
            {"evidence_id": f"E{i}", "excerpt": "界" * 240, "relation": "界" * 240}
            for i in range(3)
        ],
        "blocking_gaps": [],
    }
    with pytest.raises(ValidationError, match="2,000-byte"):
        SupportAssessment.model_validate(value)


def test_legacy_saved_results_have_unknown_support_and_are_not_enriched():
    incident, data = example("case-01", "hypothesis")
    data.pop("support_assessment")
    data.pop("source_ids")
    before = deepcopy(data)
    diagnosis = validate_saved_diagnosis(incident, json.dumps(data))
    assert diagnosis.support_assessment is None and diagnosis.source_ids == []
    assert restore_update({"diagnosis": data})["diagnosis"].support_assessment is None
    assert data == before
    with pytest.raises(DiagnosisError):
        _validate_diagnosis(incident, json.dumps(data))
    assert "support_assessment" in GenerationDiagnosis.model_json_schema()["required"]


def test_exact_quote_with_false_relation_can_still_pass_local_rules():
    incident, data = example("case-08", "hypothesis")
    data["probable_cause"] = "DNS errors caused the missing request mapping."
    data["support_assessment"]["evidence_links"][0]["relation"] = (
        "These errors prove why request mapping is absent."
    )
    # Deliberately false interpretation. The validator checks provenance, not entailment.
    result = _validate_diagnosis(incident, json.dumps(data))
    assert result.probable_cause == data["probable_cause"]
    assert (
        result.support_assessment.evidence_links[0].excerpt
        == incident.evidence[0].content
    )


def test_saved_support_audit_separates_internal_consistency_quotes_and_semantics():
    spec = importlib.util.spec_from_file_location(
        "support_capture", ROOT / "evals/rag/suite.py"
    )
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    incident, data = example("case-08", "hypothesis")
    data["support_assessment"]["evidence_links"][0]["relation"] = (
        "This proves an unsupported causal link."
    )
    flags = suite.saved_support_checks(incident, data)
    assert flags["support_internal_consistency_valid"] is True
    assert flags["support_excerpt_membership_valid"] is True
    assert flags["semantic_support_verified"] is None
    data["support_assessment"]["evidence_links"][0]["excerpt"] = "Invented text"
    assert (
        suite.saved_support_checks(incident, data)["support_excerpt_membership_valid"]
        is False
    )
    data.pop("support_assessment")
    flags = suite.saved_support_checks(incident, data)
    assert flags["support_record_available"] is False
    assert flags["support_internal_consistency_valid"] is None
    assert flags["support_excerpt_membership_valid"] is None


def test_old_generation_request_is_not_scored_against_new_support_requirement():
    spec = importlib.util.spec_from_file_location(
        "support_capture", ROOT / "evals/rag/suite.py"
    )
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    _, data = example("case-01", "hypothesis")
    data.pop("support_assessment")
    record = {
        "attempts": [
            {
                "phase": "diagnosis",
                "status": "response_received",
                "attempt": 1,
                "response_index": 0,
            }
        ],
        "requests": [
            {
                "response_format": {
                    "json_schema": {"schema": {"required": ["source_ids"]}}
                }
            }
        ],
        "responses": [[{"message": {"content": json.dumps(data)}}]],
    }
    assert suite.recorded_generation_contract_valid(record) is None


def test_read_only_http_and_cli_inspection_preserve_saved_support(monkeypatch, capsys):
    from fastapi.testclient import TestClient
    from incidentclue import cli, lifecycle, persistence
    from incidentclue.api import routes
    from incidentclue.api.app import create_app

    incident, data = example("case-01", "hypothesis")
    diagnosis = _validate_diagnosis(incident, json.dumps(data))
    identifier = "11111111-1111-4111-8111-111111111111"
    view = {
        "investigation_id": identifier,
        "incident_id": incident.incident_id,
        "status": "completed",
        "executing": False,
        "recovery_needed": False,
        "compatible": True,
        "pause_requested": False,
        "model": "saved-model",
        "error_code": None,
        "checkpoint_id": "checkpoint",
        "next_nodes": [],
        "attempts": [],
        "charged": {"model": 3, "tool": 1},
        "limits": {"model": 3, "tool": 2},
        "created_at": "2026-11-04T00:00:00Z",
        "updated_at": "2026-11-04T00:00:00Z",
        "state": json_data({"diagnosis": diagnosis, "evidence": incident.evidence}),
    }

    class SavedService:
        def __init__(self, *args, **kwargs):
            pass

        def inspect(self, value):
            assert value == identifier
            return deepcopy(view)

    @contextmanager
    def database(_):
        yield object()

    monkeypatch.setattr(routes, "Lifecycle", SavedService)
    response = TestClient(create_app(database_factory=database)).get(
        f"/investigations/{identifier}"
    )
    assert response.status_code == 200
    assert (
        response.json()["state"]["diagnosis"]["support_assessment"]
        == data["support_assessment"]
    )
    monkeypatch.setattr(lifecycle, "Lifecycle", SavedService)
    monkeypatch.setattr(persistence, "open_database", database)
    monkeypatch.setattr(cli, "_load_environment", lambda _: None)
    monkeypatch.setattr(
        cli,
        "create_groq_client",
        lambda *a: pytest.fail("Inspection constructed provider"),
    )
    assert cli.main(["inspect", identifier]) == 0
    assert json.loads(capsys.readouterr().out)["state"][
        "diagnosis"
    ] == diagnosis.model_dump(mode="json")
