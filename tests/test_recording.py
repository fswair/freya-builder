from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import yaml
from autobench import RunStatus, build_report, replay_experiment
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from freya_hackathon.recording import capture_async, capture_sync, case_task, export_cases


def records(directory: Path, kind: str) -> list[Path]:
    return list((directory / "autobench").glob(f"{kind}-*"))


def test_native_model_http_capture_redaction_and_replay(tmp_path: Path, monkeypatch) -> None:
    secret = "test-team-secret-not-for-records"
    monkeypatch.setenv("FREYA_BANK_TEAM_KEY", secret)
    directory = tmp_path / "candidate"
    calls = []
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: (calls.append(request.method), httpx.Response(200))[1]
        )
    )

    async def action():
        directory.mkdir()
        (directory / "workflow.json").write_text(
            json.dumps({"headers": {"x-team-key": secret}, "code": f"key={secret}"})
        )
        (directory / "provenance").mkdir()
        (directory / "provenance" / "same-workflow.json").write_bytes(
            (directory / "workflow.json").read_bytes()
        )
        agent = Agent(TestModel(custom_output_text="ready"), instructions="Answer briefly.")
        result = await agent.run("Say ready.")
        client.post(
            "https://bank.example.test/private-path",
            headers={"x-team-key": secret},
            json={"secret": secret},
        )
        return result.output

    try:
        assert asyncio.run(capture_async(directory, "builder", action)) == "ready"
    finally:
        client.close()
    record = records(directory, "builder")[0]
    before = {p.relative_to(record): p.read_bytes() for p in record.rglob("*") if p.is_file()}
    assert secret.encode() not in b"".join(before.values())
    replayed = replay_experiment(record)
    run = replayed.runs[0]
    assert run.status == RunStatus.PASSED
    assert run.trace is not None
    operations = {span.operation for span in run.trace.spans}
    assert "pydantic_ai.agent.run" in operations
    assert any("httpx" in operation for operation in operations)
    assert any("workflow.json" == artifact.name for artifact in run.task_result.artifacts)
    assert all(
        artifact.name != "provenance/same-workflow.json" for artifact in run.task_result.artifacts
    )
    manifest = next(a for a in run.task_result.artifacts if a.name == "evidence-manifest")
    entries = yaml.safe_load((record / str(manifest.value)).read_text())["payload"]
    alias = next(entry for entry in entries if entry["path"] == "provenance/same-workflow.json")
    assert alias["artifact"] == "workflow.json"
    assert alias["source_sha256"] == next(
        entry["source_sha256"] for entry in entries if entry["path"] == "workflow.json"
    )
    assert run.asset_uses
    build_report(replayed)
    assert calls == ["POST"]
    assert before == {
        p.relative_to(record): p.read_bytes() for p in record.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_operation_retains_evidence_and_original_exception(tmp_path: Path, cancel) -> None:
    directory = tmp_path / "failed"
    error = asyncio.CancelledError() if cancel else TimeoutError("original timeout")

    async def action():
        directory.mkdir()
        (directory / "trace.jsonl").write_text('{"event":"before_failure"}\n')
        raise error

    with pytest.raises(type(error)) as caught:
        asyncio.run(capture_async(directory, "builder", action))
    assert caught.value is error
    replayed = replay_experiment(records(directory, "builder")[0])
    assert replayed.runs[0].status == RunStatus.FAILED
    assert replayed.runs[0].task_result.error is not None
    assert any(
        artifact.name == "trace.jsonl" for artifact in replayed.runs[0].task_result.artifacts
    )


def test_nested_pipeline_keeps_one_execution_record(tmp_path: Path) -> None:
    directory = tmp_path / "pipeline"

    async def builder():
        directory.mkdir()
        return "built"

    def pipeline():
        assert asyncio.run(capture_async(directory, "builder", builder)) == "built"
        return capture_sync(directory, "practice", lambda: {"passed": 1})

    assert capture_sync(directory, "pipeline", pipeline) == {"passed": 1}
    assert len(list((directory / "autobench").iterdir())) == 1
    replayed = replay_experiment(records(directory, "pipeline")[0])
    assert replayed.runs[0].trace is not None
    operations = {span.operation for span in replayed.runs[0].trace.spans}
    assert {"freya.pipeline", "freya.builder", "freya.practice"} <= operations


def fixture_run(root: Path, passed: bool, *, snekbox: bool = False) -> Path:
    practice = root / "practice"
    practice.mkdir(parents=True)
    (root / "workflow.json").write_text('{"initial_node":"test"}')
    (practice / "cases.json").write_text('[{"id":"PUB-01","brief":"A"}]')
    row = {
        "id": "PUB-01",
        "pass": passed,
        "failed": [] if passed else ["no promise"],
        "call_id": "call-1",
        "customer_id": "customer-1",
    }
    (practice / "results.json").write_text(json.dumps({"rows": [row]}))
    events = [{"event": "route_evaluated", "error": "Failed to reach snekbox"}]
    if snekbox:
        events.append({"event": "function_call_error", "error": "Failed to reach snekbox"})
    (practice / "call-call-1.json").write_text(
        json.dumps(
            {"call": {"messages": [{"role": "system", "message": json.dumps(e)} for e in events]}}
        )
    )
    (practice / "ledger-customer-1.json").write_text('{"promises":[]}')
    return root


def test_extraction_and_rejected_reset_diagnostics_do_not_change_verdict(tmp_path, monkeypatch):
    from autobench import Case

    directory = fixture_run(tmp_path / "observed", True)
    events = [
        {"event": "extraction_error", "error": ""},
        {"event": "extraction_failed"},
        {
            "event": "function_call_complete",
            "result": {
                "status": "success",
                "validation_errors": ["decision rejected", "name rejected"],
            },
        },
        {"event": "route_evaluated", "validation_errors": ["not a function result"]},
    ]
    (directory / "practice/call-call-1.json").write_text(
        json.dumps(
            {
                "call": {
                    "messages": [
                        {"role": "system", "message": json.dumps(event)} for event in events
                    ]
                }
            }
        )
    )
    metrics = {}

    class Context:
        def factor(self, name):
            return str(directory)

        def metric(self, name, value, **kwargs):
            metrics[name] = (value, kwargs)

        def artifact(self, *args, **kwargs):
            pass

    monkeypatch.setattr("freya_hackathon.recording.attach_files", lambda *args: None)
    result = case_task(cast(Any, Context()), Case(id="PUB-01", input={}))
    assert result["passed"] is True
    assert result["failed"] == []
    assert result["snekbox_errors"] == 0
    assert metrics["freya.extraction_warnings"][0] == 2
    assert metrics["freya.variable_validation_errors"][0] == 2
    assert all(kwargs["role"].value == "diagnostic" for _, kwargs in metrics.values())


def test_case_import_preserves_official_failures_and_is_self_contained(tmp_path: Path) -> None:
    old = fixture_run(tmp_path / "old", False, snekbox=True)
    new = fixture_run(tmp_path / "new", True)
    record = tmp_path / "comparison"
    export_cases([old, new], record)
    old.rename(tmp_path / "moved-old")
    new.rename(tmp_path / "moved-new")
    experiment = replay_experiment(record)
    assert len(experiment.runs) == 2
    runs = {run.variant_id: run for run in experiment.runs}
    assert runs["old"].task_result.output == {
        "passed": False,
        "failed": ["no promise"],
        "snekbox_errors": 1,
    }
    assert runs["new"].task_result.output["snekbox_errors"] == 0
    assert len(runs["old"].scores) == len(runs["new"].scores) == 1
    assert runs["old"].scores[0].value == 0.0
    assert runs["new"].scores[0].value == 1.0
    assert runs["old"].evaluation_status == "failed"
    assert runs["new"].evaluation_status == "passed"
    assert any(
        artifact.name == "practice/ledger-customer-1.json"
        for artifact in runs["old"].task_result.artifacts
    )
    report = build_report(experiment)
    assert report.evaluation is not None
    assert report.evaluation.passed_count == 1
    assert report.evaluation.failed_count == 1
    assert report.evaluation.pass_rate == 0.5
    with pytest.raises(FileExistsError):
        export_cases([old, new], record)


def test_case_comparison_rejects_different_cases(tmp_path: Path) -> None:
    old = fixture_run(tmp_path / "old", False)
    new = fixture_run(tmp_path / "new", True)
    (new / "practice" / "cases.json").write_text('[{"id":"PUB-02","brief":"A"}]')
    with pytest.raises(ValueError, match="identical selected"):
        export_cases([old, new], tmp_path / "comparison")


def test_module_cli_import_preserves_large_artifact(tmp_path: Path) -> None:
    directory = tmp_path / "historical"
    directory.mkdir()
    payload = {"large": "evidence" * 700_000}
    (directory / "workflow.json").write_text(json.dumps(payload))
    result = subprocess.run(
        [sys.executable, "-m", "freya_hackathon.recording", "import", str(directory)],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    record = records(directory, "import")[0]
    experiment = replay_experiment(record)
    artifact = next(
        item for item in experiment.runs[0].task_result.artifacts if item.name == "workflow.json"
    )
    stored = yaml.safe_load((record / str(artifact.value)).read_text())
    assert stored["payload"] == payload
