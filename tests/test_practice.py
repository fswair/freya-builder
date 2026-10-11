from __future__ import annotations

import json
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import httpx
import pytest

from freya_hackathon.builder import write_json
from freya_hackathon.practice import Practice, preserve_simulation_metadata


def runner(tmp_path: Path, handler) -> Practice:
    practice = Practice.__new__(Practice)
    practice.run = tmp_path
    practice.output = tmp_path
    practice.deadline = monotonic() + 30
    practice.freya = SimpleNamespace(
        URL="https://example.test",
        HEADERS={},
        AGENT_LLM={"provider": "experimental-5", "model": "experimental-5"},
        SIM_LLM={"provider": "google", "model": "gemini-3-flash-preview"},
    )
    practice.client = httpx.Client(transport=httpx.MockTransport(handler))
    return practice


def test_http_200_failure_is_not_accepted(tmp_path: Path) -> None:
    practice = runner(tmp_path, lambda _: httpx.Response(200, json={"success": False}))
    try:
        with pytest.raises(RuntimeError, match="rejected"):
            practice.api("POST", "/api/v2/workflow", {})
        assert (tmp_path / "api-rejection.json").exists()
    finally:
        practice.client.close()


@pytest.mark.parametrize("change", ["handler", "url", "prompt"])
def test_changed_export_stops_practice_before_any_simulation(tmp_path, change):
    requests = []

    def handler(request):
        requests.append(request)
        raise AssertionError("No API request is allowed after an export mismatch")

    practice = runner(tmp_path, handler)
    workflow = {"nodes": {"speak": {
        "task_message": "Verify identity first.",
        "tools": [{"function": {"handler": "__function__:api_call", "parameters": {
            "properties": {"url": {"default": "https://bank.example/verify_identity"}}
        }}}],
    }}}
    exported = json.loads(json.dumps(workflow))
    node = exported["nodes"]["speak"]
    if change == "handler":
        node["tools"][0]["function"]["handler"] = "__function__:end_call"
    elif change == "url":
        node["tools"][0]["function"]["parameters"]["properties"]["url"]["default"] = (
            "https://bank.example/create_promise"
        )
    else:
        node["task_message"] = "Skip identity."
    write_json(tmp_path / "workflow.json", workflow)
    practice.freya.validate_workflow = lambda *_: {"valid": True}
    practice.freya.save_workflow = lambda *_: {
        "workflow_id": "workflow", "agent_id": "agent", "version": 1
    }
    practice.freya.export_workflow = lambda _id, path: write_json(
        Path(path), {"workflow": exported}
    )
    try:
        with pytest.raises(ValueError, match="Export changed generated value"):
            practice._execute(tmp_path / "not-read-cases.json", "A", "synthetic-slot")
    finally:
        practice.client.close()
    assert requests == []
    assert not (tmp_path / "simulation.json").exists()


def test_custom_personas_use_registered_bank_seed_without_changing_case_id(
    tmp_path: Path, monkeypatch
) -> None:
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"variables": {"customer_id": "synthetic-1"}})

    monkeypatch.setenv("FREYA_BANK_TEAM_KEY", "synthetic-team-key")
    practice = runner(tmp_path, handler)
    practice.session_cases = {"DEV-CALLBACK": "PUB-02"}
    body = {"case": "DEV-CALLBACK"}
    try:
        practice.bank("/v1/practice/sessions", body)
        practice.bank("/v1/practice/sessions", {"case": "PUB-01"})
    finally:
        practice.client.close()
    assert body == {"case": "DEV-CALLBACK"}
    assert requests == [{"case": "PUB-02"}, {"case": "PUB-01"}]
    event = json.loads((tmp_path / "trace.jsonl").read_text())
    assert event["case_id"] == "DEV-CALLBACK"
    assert event["seed_case_id"] == "PUB-02"


def test_simulation_is_text_only_and_records_id_before_polling(tmp_path: Path, monkeypatch) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["mode"] == "chat"
            assert body["assistantLlmOverride"]["model"] == "experimental-5"
            assert body["runsPerPersona"] == 1
            value = {"simulation": {"id": "sim-1"}}
        elif request.url.path.endswith("iterations"):
            value = {"iterations": [{"id": "iteration-1"}]}
        else:
            assert (
                json.loads((tmp_path / "simulation.json").read_text())["simulation_id"] == "sim-1"
            )
            value = {"simulation": {"status": "completed"}}
        return httpx.Response(200, json={"success": True, "data": {"result": value}})

    practice = runner(tmp_path, handler)
    monkeypatch.setattr("freya_hackathon.practice.time.sleep", lambda _: None)
    try:
        assert practice.simulate("agent", "test", {"persona": {}}) == (
            "sim-1",
            "iteration-1",
            "completed",
        )
        assert len(requests) == 3
    finally:
        practice.client.close()


def test_simulator_error_keeps_raw_reason_run_id_and_combined_failures() -> None:
    reason = "Analysis did not arrive. " + "detail " * 100
    row = {"id": "DEV", "call_id": "call-1", "pass": False, "failed": ["missing promise"]}
    iteration = {
        "runs": [
            {
                "id": "run-1",
                "personaName": "DEV synthetic case",
                "callId": "call-1",
                "outcome": "error",
                "reason": reason,
            }
        ]
    }
    preserve_simulation_metadata([row], iteration)
    assert row["simulation_run_id"] == "run-1"
    assert row["simulation_outcome"] == "error"
    assert row["simulation_reason"] == reason
    assert row["pass"] is False
    assert row["failed"] == ["missing promise", "conversation: " + reason[:400]]


@pytest.mark.parametrize("problem", ["missing", "duplicate", "call_mismatch", "missing_id"])
def test_iteration_join_never_silently_selects_missing_or_ambiguous_run(problem: str) -> None:
    judge = {"id": "run-1", "personaName": "DEV case", "callId": "call-1", "outcome": "pass"}
    runs = [judge]
    if problem == "missing":
        runs = []
    elif problem == "duplicate":
        runs.append({**judge, "id": "run-2"})
    elif problem == "call_mismatch":
        judge["callId"] = "different-call"
    else:
        del judge["id"]
    row = {"id": "DEV", "call_id": "call-1", "pass": True, "failed": []}
    preserve_simulation_metadata([row], {"runs": runs})
    assert row["pass"] is False
    assert row["simulation_join_error"]
    assert len(row["failed"]) == 1
    if problem in {"missing", "duplicate"}:
        assert row["simulation_run_id"] is None
        assert row["simulation_outcome"] is None


@pytest.mark.parametrize("collect", [False, True])
def test_normal_and_recovery_reports_preserve_structured_simulator_error(
    tmp_path: Path, monkeypatch, collect: bool
) -> None:
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        assert request.method == "GET"
        if request.url.path.endswith("iterations"):
            value = {"iterations": [{"id": "iteration-1"}]}
        elif "/versions/" in request.url.path:
            value = {"versions": [{"versionNumber": 1, "isPublished": True}]}
        elif "/ledger/" in request.url.path:
            value = {"calls": []}
        else:
            value = {"status": "completed"}
        return httpx.Response(200, json=value)

    monkeypatch.setenv("FREYA_BANK_TEAM_KEY", "synthetic-team-key")
    monkeypatch.setattr("freya_hackathon.practice.ROOT", tmp_path)
    monkeypatch.setattr("freya_hackathon.practice.audit_runtime", lambda _run: {})
    practice = runner(tmp_path, handler)
    practice.output = tmp_path / "practice"
    practice.output.mkdir()
    reason = "Analysis did not arrive within 300 seconds."
    iteration = {
        "runsTotal": 1,
        "runs": [
            {
                "id": "run-1",
                "personaName": "DEV synthetic case",
                "callId": "call-1",
                "outcome": "error",
                "reason": reason,
            }
        ],
    }
    saved = {"workflow_id": "workflow", "agent_id": "agent", "version": 1}
    cases = [{"id": "DEV", "brief": "A", "identity": {}}]
    write_json(tmp_path / "workflow.json", {"nodes": {}})
    write_json(practice.output / "cases.json", cases)
    write_json(practice.output / "deployment.json", saved)
    write_json(practice.output / "simulation.json", {"simulation_id": "sim-1"})
    practice.freya.get_iteration = lambda *_: iteration
    practice.freya.get_call = lambda _: {
        "messages": [
            {
                "role": "system",
                "message": json.dumps({"variable_table": {"customer_id": "customer-1"}}),
            }
        ]
    }
    practice.freya.validate_workflow = lambda *_: {"valid": True}
    practice.freya.save_workflow = lambda *_: saved
    practice.freya.export_workflow = lambda _id, path: write_json(
        Path(path), {"workflow": {"nodes": {}}}
    )
    practice.freya.workspace_id = lambda: "workspace"
    practice.freya.create_personas = lambda _: {"personas": [{"id": "persona"}]}
    practice.cases = SimpleNamespace(
        score_ledger=lambda *_: {"failed": []},
        persona_for=lambda *_: {"name": "DEV synthetic case"},
        run_once=lambda *_: (
            [{"id": "DEV", "call_id": "call-1", "pass": True, "failed": []}],
            "sim-1",
            "iteration-1",
            "completed",
            1.0,
            0.1,
        ),
    )
    try:
        report = (
            practice._collect()
            if collect
            else practice._execute(practice.output / "cases.json", "A", "synthetic-slot")
        )
    finally:
        practice.client.close()
    row = report["rows"][0]
    assert report["passed"] == 0
    assert row["simulation_run_id"] == "run-1"
    assert row["simulation_outcome"] == "error"
    assert row["simulation_reason"] == reason
    assert row["simulation_match_count"] == 1
    assert row["pass"] is False
    assert json.loads((practice.output / "iteration.json").read_text()) == iteration
    assert json.loads((practice.output / "results.json").read_text())["rows"][0] == row
    assert requests and all(method == "GET" for method, _ in requests)
