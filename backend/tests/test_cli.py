"""CLI input/output, provider failure, and local-record behavior."""

from importlib.metadata import entry_points
import json
import subprocess
import sys
from uuid import UUID

import httpx2
import pytest
from openai import OpenAI

from incidentclue import cli
from incidentclue.llm import DEFAULT_MODEL, GROQ_BASE_URL
from incidentclue.records import write_run_record


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)


@pytest.fixture
def incident_file(tmp_path, incident_data):
    path = tmp_path / "incident.json"
    path.write_text(json.dumps(incident_data), encoding="utf-8")
    return path


@pytest.fixture
def provider(monkeypatch, diagnosis_data):
    monkeypatch.setenv("GROQ_API_KEY", "test-cli-key")
    state = {
        "calls": [],
        "keys": [],
        "status": 200,
        "timeout": False,
        "payload": {
            "id": "chatcmpl-cli-test",
            "object": "chat.completion",
            "created": 1,
            "model": DEFAULT_MODEL,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(diagnosis_data),
                    },
                    "finish_reason": "stop",
                }
            ],
        },
    }

    def handler(request):
        state["calls"].append(json.loads(request.content))
        if state["timeout"]:
            raise httpx2.ReadTimeout("Private transport detail.", request=request)
        return httpx2.Response(state["status"], json=state["payload"])

    def create_client(key):
        state["keys"].append(key)
        return OpenAI(
            api_key=key,
            base_url=GROQ_BASE_URL,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        )

    monkeypatch.setattr(cli, "create_groq_client", create_client)
    return state


def test_prints_only_valid_diagnosis_json(incident_file, provider, capsys):
    assert cli.main(["diagnose", str(incident_file)]) == 0

    output = capsys.readouterr()
    diagnosis = json.loads(output.out)
    assert diagnosis["status"] == "hypothesis"
    assert diagnosis["evidence_ids"] == ["E2"]
    assert output.err == ""
    assert len(provider["calls"]) == 1
    assert provider["calls"][0]["model"] == DEFAULT_MODEL


@pytest.mark.parametrize("argv", [[], ["diagnose"]])
def test_requires_command_and_incident_path(argv, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(argv)
    assert error.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_reports_missing_file_before_contacting_provider(tmp_path, provider, capsys):
    assert cli.main(["diagnose", str(tmp_path / "missing.json")]) == 2
    output = capsys.readouterr()
    assert "Could not read the incident file" in output.err
    assert output.out == ""
    assert provider["calls"] == []


@pytest.mark.parametrize("text", ['{"private-input":', '{"summary":"private-input"}'])
def test_rejects_invalid_input_without_echoing_it(
    incident_file, provider, capsys, text
):
    incident_file.write_text(text, encoding="utf-8")

    assert cli.main(["diagnose", str(incident_file)]) == 2
    output = capsys.readouterr()
    assert "required fields or validation rules" in output.err
    assert "private-input" not in output.err
    assert provider["calls"] == []


def test_rejects_non_utf8_file(incident_file, provider, capsys):
    incident_file.write_bytes(b"\xff\xfe")
    assert cli.main(["diagnose", str(incident_file)]) == 2
    assert "UTF-8" in capsys.readouterr().err
    assert provider["calls"] == []


def test_bounds_raw_file_read(incident_file, provider, capsys):
    incident_file.write_bytes(b" " * (cli.MAX_INPUT_FILE_BYTES + 1))
    assert cli.main(["diagnose", str(incident_file)]) == 2
    assert "file limit" in capsys.readouterr().err
    assert provider["calls"] == []


def test_maps_serialized_input_budget_failure_to_input_error(
    incident_file, incident_data, provider, capsys
):
    incident_data["evidence"][0]["content"] = "x" * cli.MAX_INCIDENT_BYTES
    incident_file.write_text(json.dumps(incident_data), encoding="utf-8")

    assert cli.main(["diagnose", str(incident_file)]) == 2
    assert "byte input limit" in capsys.readouterr().err
    assert provider["calls"] == []


def test_rejects_non_utf8_configuration(incident_file, tmp_path, provider, capsys):
    environment = tmp_path / "invalid.env"
    environment.write_bytes(b"\xff\xfe")
    assert cli.main([
        "diagnose", str(incident_file), "--env-file", str(environment)
    ]) == 2
    assert "Could not read the environment file" in capsys.readouterr().err
    assert provider["calls"] == []


def test_reports_missing_key_without_implicitly_loading_a_file(incident_file, capsys):
    assert cli.main(["diagnose", str(incident_file)]) == 2
    output = capsys.readouterr()
    assert "Set GROQ_API_KEY" in output.err
    assert output.out == ""


def test_reports_missing_env_file(incident_file, tmp_path, provider, capsys):
    assert cli.main(
        ["diagnose", str(incident_file), "--env-file", str(tmp_path / "missing.env")]
    ) == 2
    assert "environment file does not exist" in capsys.readouterr().err
    assert provider["calls"] == []


@pytest.mark.parametrize(
    ("existing_model", "model_args", "expected"),
    [
        (None, [], "file-model"),
        ("environment-model", [], "environment-model"),
        ("environment-model", ["--model", "cli-model"], "cli-model"),
    ],
)
def test_configuration_precedence(
    incident_file, tmp_path, provider, monkeypatch, capsys,
    existing_model, model_args, expected,
):
    monkeypatch.delenv("GROQ_API_KEY")
    if existing_model is not None:
        monkeypatch.setenv("GROQ_MODEL", existing_model)
    env_file = tmp_path / "provider.env"
    env_file.write_text("GROQ_API_KEY=file-test-key\nGROQ_MODEL=file-model")

    assert cli.main(
        ["diagnose", str(incident_file), "--env-file", str(env_file)] + model_args
    ) == 0
    output = capsys.readouterr()
    assert provider["keys"] == ["file-test-key"]
    assert provider["calls"][0]["model"] == expected
    assert "file-test-key" not in output.out + output.err


@pytest.mark.parametrize("status", [401, 429, 500])
def test_reports_provider_errors_without_echoing_response_body(
    incident_file, provider, capsys, status
):
    provider.update(status=status, payload={"error": {"message": "Private provider detail."}})
    assert cli.main(["diagnose", str(incident_file)]) == 1
    output = capsys.readouterr()
    assert f"HTTP {status}" in output.err
    assert "Private provider detail" not in output.err
    assert output.out == ""
    assert len(provider["calls"]) == 1


def test_reports_timeout(incident_file, provider, capsys):
    provider["timeout"] = True
    assert cli.main(["diagnose", str(incident_file)]) == 1
    output = capsys.readouterr()
    assert "APITimeoutError" in output.err
    assert "Private transport detail" not in output.err
    assert len(provider["calls"]) == 1


def test_rejects_untrusted_citation_without_printing_a_diagnosis(
    incident_file, provider, diagnosis_data, capsys
):
    diagnosis_data["evidence_ids"] = ["E404"]
    diagnosis_data["support_assessment"]["evidence_links"][0]["evidence_id"] = "E404"
    provider["payload"]["choices"][0]["message"]["content"] = json.dumps(diagnosis_data)

    assert cli.main(["diagnose", str(incident_file)]) == 1
    output = capsys.readouterr()
    assert "outside the supplied incident" in output.err
    assert output.out == ""


def test_abstention_is_a_successful_result(
    incident_file, provider, diagnosis_data, capsys
):
    diagnosis_data.update(status="insufficient_evidence", probable_cause=None, evidence_ids=[])
    diagnosis_data["support_assessment"] = {"evidence_links": [], "blocking_gaps": ["No mechanism has been established."]}
    provider["payload"]["choices"][0]["message"]["content"] = json.dumps(diagnosis_data)

    assert cli.main(["diagnose", str(incident_file)]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["probable_cause"] is None
    assert output.err == ""


def test_records_success_without_secrets_or_raw_input(
    incident_file, incident_data, tmp_path, provider, capsys
):
    incident_data["incident_id"] = "../outside"
    incident_file.write_text(json.dumps(incident_data), encoding="utf-8")
    directory = tmp_path / "runs"

    assert cli.main(
        ["diagnose", str(incident_file), "--record-dir", str(directory)]
    ) == 0
    output = capsys.readouterr()
    paths = list(directory.glob("*.json"))
    assert len(paths) == 1
    record_text = paths[0].read_text()
    record = json.loads(record_text)
    assert paths[0].stem == str(UUID(record["run_id"]))
    assert paths[0].parent == directory
    assert record["outcome"] == "success"
    assert record["diagnosis"] == json.loads(output.out)
    assert record["input"]["serialized_sha256"]
    assert "evidence" not in record["input"]
    assert record["provider_usage"] is None
    assert record["provider_response_id"] is None
    assert "test-cli-key" not in record_text + output.out + output.err
    assert "Run record:" in output.err


def test_records_provider_failure_safely(incident_file, tmp_path, provider, capsys):
    provider.update(status=401, payload={"error": {"message": "Private provider detail."}})
    directory = tmp_path / "runs"

    assert cli.main(
        ["diagnose", str(incident_file), "--record-dir", str(directory)]
    ) == 1
    output = capsys.readouterr()
    record_text = next(directory.glob("*.json")).read_text()
    record = json.loads(record_text)
    assert record["outcome"] == "provider_error"
    assert record["provider_http_status"] == 401
    assert record["exit_code"] == 1
    assert "Private provider detail" not in record_text + output.err
    assert "test-cli-key" not in record_text + output.err
    assert output.out == ""


def test_records_input_failure_before_a_request(tmp_path, provider, capsys):
    directory = tmp_path / "runs"
    assert cli.main(
        ["diagnose", str(tmp_path / "missing.json"), "--record-dir", str(directory)]
    ) == 2
    capsys.readouterr()
    record = json.loads(next(directory.glob("*.json")).read_text())
    assert record["outcome"] == "input_error"
    assert record["exit_code"] == 2
    assert "serialized_sha256" not in record["input"]
    assert provider["calls"] == []


def test_rejects_invalid_record_directory_before_a_request(
    incident_file, tmp_path, provider, capsys
):
    blocked = tmp_path / "blocked"
    blocked.write_text("existing file")
    assert cli.main(
        ["diagnose", str(incident_file), "--record-dir", str(blocked)]
    ) == 2
    assert "prepare the run-record directory" in capsys.readouterr().err
    assert blocked.read_text() == "existing file"
    assert provider["calls"] == []


def test_record_write_failure_keeps_diagnosis_output(
    incident_file, tmp_path, provider, monkeypatch, capsys
):
    def fail_write(path, record):
        raise OSError("Private disk detail.")

    monkeypatch.setattr(cli, "write_run_record", fail_write)
    assert cli.main(
        ["diagnose", str(incident_file), "--record-dir", str(tmp_path / "runs")]
    ) == 3
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "hypothesis"
    assert "Could not write the run record" in output.err
    assert "Private disk detail" not in output.err


def test_record_writer_refuses_to_overwrite_existing_file(tmp_path):
    path = tmp_path / "record.json"
    write_run_record(path, {"run_id": "first"})
    with pytest.raises(FileExistsError):
        write_run_record(path, {"run_id": "second"})
    assert json.loads(path.read_text()) == {"run_id": "first"}


def test_module_help_works_outside_the_repository(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "incidentclue.cli", "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0
    assert "diagnose" in result.stdout
    assert result.stderr == ""


def test_installed_console_entry_point_targets_the_cli():
    entry = entry_points(group="console_scripts", name="incidentclue")
    assert len(entry) == 1
    assert next(iter(entry)).load() is cli.main
