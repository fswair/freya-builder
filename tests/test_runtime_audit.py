import hashlib
import json
import shutil
from pathlib import Path

import pytest

from freya_hackathon.runtime_audit import (
    API_RESULT_ERROR_SIGNATURE,
    API_STATUS_ERROR_SIGNATURE,
    BANK_WORKER_SIGNATURE,
    EXTRACTION_ERROR_SIGNATURE,
    INFRASTRUCTURE_SIGNATURE,
    MISSING_ANALYSIS_SIGNATURE,
    PYTHON_TRACEBACK_SIGNATURE,
    SANDBOX_LIMIT_SIGNATURE,
    UNRESOLVED_SIGNATURE,
    audit_runtime,
)


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value))


def _fixture(tmp_path: Path, *, passed: bool = True) -> tuple[Path, dict, dict]:
    practice = tmp_path / "practice"
    practice.mkdir()
    result = {
        "passed": int(passed),
        "total": 1,
        "rows": [{"id": "test", "call_id": "one", "pass": passed}],
    }
    iteration = {
        "runsTotal": 1,
        "runs": [
            {
                "id": "run-one",
                "callId": "one",
                "outcome": "pass" if passed else "fail",
                "reason": "",
            }
        ],
    }
    _write(practice / "results.json", result)
    _write(practice / "iteration.json", iteration)
    _write(
        practice / "call-one.json",
        {"call": {"id": "one", "messages": [{"role": "assistant", "message": "Hello"}]}},
    )
    return practice, result, iteration


def _event(error: str, *, function: str = "code_exec", role: str = "system") -> dict:
    return {
        "role": role,
        "nodeId": "decide",
        "secondsFromStart": 2.5,
        "message": json.dumps(
            {"event": "function_call_error", "function": function, "error": error}
        ),
    }


def _messages(practice: Path, messages: list) -> None:
    _write(practice / "call-one.json", {"call": {"id": "one", "messages": messages}})


def test_infra_audit_counts_events_without_discarding_success_or_changing_score(tmp_path) -> None:
    p, result, _ = _fixture(tmp_path)
    error = "Failed to reach snekbox: Name or service not known"
    messages = [
        {"role": "system", "message": json.dumps(event)}
        for event in [
            {"event": "function_call_error", "function": "code_exec", "error": error},
            {"event": "route_evaluated", "variable_table": {"old_error": error}},
            {"event": "function_call_error", "function": "code_exec", "error": "SyntaxError"},
        ]
    ]
    _messages(p, messages)
    report = audit_runtime(tmp_path)
    assert len(report["cases"][0]["infrastructure_errors"]) == 1
    assert report["infrastructure_affected_cases"] == ["test"]
    assert report["usable_as_clean_model_comparison"] is False
    assert report["official_passed"] == 1
    assert json.loads((p / "results.json").read_text()) == result


@pytest.mark.parametrize("function", ["api_call", "code_exec"])
@pytest.mark.parametrize("passed", [True, False])
def test_parse_failure_is_unresolved_even_in_official_pass(tmp_path, function, passed) -> None:
    p, result, _ = _fixture(tmp_path, passed=passed)
    error = UNRESOLVED_SIGNATURE + " (returncode=0). Stdout: PRIVATE_VARIABLE_PAYLOAD"
    _messages(p, [_event(error, function=function)])
    report = audit_runtime(tmp_path)
    assert report["unresolved_runtime_error_cases"] == ["test"]
    assert report["infrastructure_affected_cases"] == []
    assert report["simulator_error_cases"] == []
    assert report["incomplete_evidence_cases"] == []
    assert report["usable_as_clean_model_comparison"] is False
    event = report["cases"][0]["unresolved_runtime_errors"][0]
    assert event == {
        "event_index": 0,
        "node": "decide",
        "seconds": 2.5,
        "function": function,
        "signature": UNRESOLVED_SIGNATURE,
        "error_sha256": hashlib.sha256(error.encode()).hexdigest(),
    }
    assert "PRIVATE_VARIABLE_PAYLOAD" not in json.dumps(report)
    assert json.loads((p / "results.json").read_text()) == result


def test_customer_quotes_snapshots_and_domain_failures_do_not_contaminate(tmp_path) -> None:
    p, _, iteration = _fixture(tmp_path, passed=False)
    events = [
        _event(signature, role=role)
        for role in ("user", "customer", "assistant")
        for signature in (INFRASTRUCTURE_SIGNATURE, UNRESOLVED_SIGNATURE)
    ]
    events.extend(
        [
            {
                "role": "system",
                "message": json.dumps(
                    {"event": "route_evaluated", "variable_table": {"old_error": events[0]}}
                ),
            },
            {"role": "system", "message": "not json"},
            {"role": "system", "message": "[]"},
            _event("known bank write rejected"),
            _event("SyntaxError"),
            _event("Customer said: " + UNRESOLVED_SIGNATURE),
            {"role": "user", "message": MISSING_ANALYSIS_SIGNATURE + " (300s)"},
        ]
    )
    iteration["runs"][0]["personaPrompt"] = MISSING_ANALYSIS_SIGNATURE
    iteration["runs"][0]["reason"] = "Customer quoted: " + MISSING_ANALYSIS_SIGNATURE
    _write(p / "iteration.json", iteration)
    _messages(p, events)
    report = audit_runtime(tmp_path)
    assert report["usable_as_clean_model_comparison"] is True
    assert report["official_passed"] == 0  # A genuine quality failure is usable evidence.
    assert all(
        report[key] == []
        for key in (
            "infrastructure_affected_cases",
            "unresolved_runtime_error_cases",
            "simulator_error_cases",
            "incomplete_evidence_cases",
        )
    )


def test_bank_worker_exception_blocks_clean_comparison_without_assigning_blame(tmp_path):
    practice, result, _ = _fixture(tmp_path)
    error = "API request failed: <html><title>Worker threw exception</title>PRIVATE</html>"
    _messages(practice, [_event(error, function="api_call")])
    report = audit_runtime(tmp_path)
    assert report["usable_as_clean_model_comparison"] is False
    assert report["unresolved_runtime_error_cases"] == ["test"]
    assert report["infrastructure_affected_cases"] == []
    assert report["cases"][0]["unresolved_runtime_errors"][0]["signature"] == BANK_WORKER_SIGNATURE
    assert "PRIVATE" not in json.dumps(report)
    assert json.loads((practice / "results.json").read_text()) == result


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_quoted_worker_error_does_not_count_as_an_actual_api_error(tmp_path, role):
    practice, _, _ = _fixture(tmp_path)
    _messages(
        practice,
        [_event("API request failed: Worker threw exception", function="api_call", role=role)],
    )
    assert audit_runtime(tmp_path)["usable_as_clean_model_comparison"] is True


def _structured(event: dict, *, node="verify_identity", role="system") -> dict:
    return {"role": role, "nodeId": node, "message": json.dumps(event)}


@pytest.mark.parametrize("role", ["system", "user", "assistant"])
def test_blank_extraction_error_is_counted_only_as_an_actual_system_event(tmp_path, role):
    practice, result, _ = _fixture(tmp_path)
    _messages(practice, [_structured({
        "event": "extraction_error", "variables": ["turn_terms"], "error": "",
        "input_text": "PRIVATE_INPUT",
    }, node="dialogue", role=role)])
    report = audit_runtime(tmp_path)
    assert report["usable_as_clean_model_comparison"] is (role != "system")
    if role == "system":
        assert report["cases"][0]["unresolved_runtime_errors"][0]["signature"] == (
            EXTRACTION_ERROR_SIGNATURE
        )
    assert report["infrastructure_affected_cases"] == []
    assert "PRIVATE_INPUT" not in json.dumps(report)
    assert json.loads((practice / "results.json").read_text()) == result


@pytest.mark.parametrize("prior", ["api_call", "code_exec", None])
@pytest.mark.parametrize("route_node", ["verify_identity", "decide"])
def test_timeout_metadata_requires_same_node_api_start_and_is_not_recounted(
    tmp_path, prior, route_node
):
    practice, result, _ = _fixture(tmp_path)
    events = [] if prior is None else [
        _structured({"event": "function_call_start", "function": prior})
    ]
    route = _structured({"event": "route_evaluated", "variable_table": {
        "verify_identity_response.status": "error",
        "verify_identity_response.status_code": 408,
        "verify_identity_response.message": "Action api_call timed out PRIVATE",
    }}, node=route_node)
    _messages(practice, [*events, route, route])
    report = audit_runtime(tmp_path)
    errors = report["cases"][0]["unresolved_runtime_errors"]
    expected = prior == "api_call" and route_node == "verify_identity"
    assert len(errors) == int(expected)
    if expected:
        assert errors[0]["signature"] == API_STATUS_ERROR_SIGNATURE
    assert report["usable_as_clean_model_comparison"] is (not expected)
    assert "PRIVATE" not in json.dumps(report)
    assert json.loads((practice / "results.json").read_text()) == result


@pytest.mark.parametrize("status", ["success", "error"])
def test_completed_api_error_is_counted_once_but_domain_denial_is_not(tmp_path, status):
    practice, _, _ = _fixture(tmp_path)
    _messages(practice, [
        _structured({"event": "function_call_start", "function": "api_call"}),
        _structured({"event": "function_call_complete", "function": "api_call", "result": {
            "status": status, "data": {"ok": False}, "status_code": 400,
        }}),
        _structured({"event": "route_evaluated", "variable_table": {
            "verify_identity_response.status": "error",
            "verify_identity_response.status_code": 400,
        }}),
    ])
    report = audit_runtime(tmp_path)
    errors = report["cases"][0]["unresolved_runtime_errors"]
    assert len(errors) == int(status == "error")
    if errors:
        assert errors[0]["signature"] == API_RESULT_ERROR_SIGNATURE


def test_function_error_and_later_timeout_snapshot_are_not_counted_twice(tmp_path):
    practice, _, _ = _fixture(tmp_path)
    _messages(practice, [
        _structured({"event": "function_call_start", "function": "api_call"}),
        _structured({"event": "function_call_error", "function": "api_call",
                     "error": "API request failed: Worker threw exception"}),
        _structured({"event": "route_evaluated", "variable_table": {
            "verify_identity_response.status": "error",
            "verify_identity_response.status_code": 408,
        }}),
    ])
    errors = audit_runtime(tmp_path)["cases"][0]["unresolved_runtime_errors"]
    assert len(errors) == 1
    assert errors[0]["signature"] == BANK_WORKER_SIGNATURE


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (
            "2026-10-11T12:42:44.819Z",
            "2026-10-11T12:29:11.312Z",
            "iteration_run_ended_before_started",
        ),
        ("2026-10-11T12:42:44.819Z", "invalid", "iteration_run_times_invalid"),
        ("2026-10-11T15:00:00+03:00", "2026-10-11T12:01:00Z", None),
    ],
)
def test_iteration_chronology_is_checked_in_absolute_time(tmp_path, start, end, expected):
    practice, result, iteration = _fixture(tmp_path)
    iteration["runs"][0].update(startedAt=start, endedAt=end)
    _write(practice / "iteration.json", iteration)
    report = audit_runtime(tmp_path)
    assert report["usable_as_clean_model_comparison"] is (expected is None)
    assert report["evidence_issues"] == ([] if expected is None else [expected])
    assert json.loads((practice / "results.json").read_text()) == result


def test_sandbox_argument_limit_blocks_clean_comparison_without_changing_score(tmp_path):
    practice, result, _ = _fixture(tmp_path)
    error = (
        SANDBOX_LIMIT_SIGNATURE
        + "131599 bytes, over the 131072-byte limit a sandboxed process can receive"
    )
    _messages(practice, [_event(error, role="user"), _event(error)])
    report = audit_runtime(tmp_path)
    assert report["unresolved_runtime_error_cases"] == ["test"]
    assert report["infrastructure_affected_cases"] == []
    assert report["usable_as_clean_model_comparison"] is False
    assert len(report["cases"][0]["unresolved_runtime_errors"]) == 1
    assert json.loads((practice / "results.json").read_text()) == result


def test_python_traceback_is_a_runtime_failure_even_if_recovery_passes_case(tmp_path):
    practice, result, _ = _fixture(tmp_path)
    error = PYTHON_TRACEBACK_SIGNATURE + "\n  PRIVATE_CODE_LINE\nRuntimeError: injected failure"
    _messages(practice, [_event(error, role="user"), _event(error)])
    report = audit_runtime(tmp_path)
    assert report["unresolved_runtime_error_cases"] == ["test"]
    assert report["infrastructure_affected_cases"] == []
    assert report["usable_as_clean_model_comparison"] is False
    assert len(report["cases"][0]["unresolved_runtime_errors"]) == 1
    assert "PRIVATE_CODE_LINE" not in json.dumps(report)
    assert json.loads((practice / "results.json").read_text()) == result


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        ("error", MISSING_ANALYSIS_SIGNATURE + " (300s)"),
        ("error", "Simulator stopped unexpectedly: PRIVATE_DETAIL"),
        ("pass", MISSING_ANALYSIS_SIGNATURE + " (300s)"),
    ],
)
def test_structured_simulator_errors_block_without_runtime_errors(
    tmp_path, outcome, reason
) -> None:
    p, _, iteration = _fixture(tmp_path)
    iteration["runs"][0].update(outcome=outcome, reason=reason)
    _write(p / "iteration.json", iteration)
    report = audit_runtime(tmp_path)
    assert report["simulator_error_cases"] == ["test"]
    assert report["infrastructure_affected_cases"] == []
    assert report["unresolved_runtime_error_cases"] == []
    assert report["usable_as_clean_model_comparison"] is False
    assert "PRIVATE_DETAIL" not in json.dumps(report)


@pytest.mark.parametrize(
    "damage",
    [
        "missing_iteration",
        "invalid_iteration",
        "missing_call",
        "invalid_call",
        "empty_messages",
        "invalid_message",
        "wrong_call_identity",
        "missing_call_id",
        "missing_run_id",
        "missing_run_call_id",
        "missing_join",
        "duplicate_join",
        "duplicate_result",
        "unmatched_run",
        "run_count",
        "result_count",
        "passed_count",
        "missing_verdict",
        "running_verdict",
        "metadata_mismatch",
        "metadata_count",
        "metadata_join_error",
    ],
)
def test_incomplete_evidence_never_becomes_clean(tmp_path, damage) -> None:
    p, result, iteration = _fixture(tmp_path)
    run = iteration["runs"][0]
    if damage == "missing_iteration":
        (p / "iteration.json").unlink()
    elif damage == "invalid_iteration":
        (p / "iteration.json").write_text("{")
    elif damage == "missing_call":
        (p / "call-one.json").unlink()
    elif damage == "invalid_call":
        _write(p / "call-one.json", {"call": None})
    elif damage == "empty_messages":
        _messages(p, [])
    elif damage == "invalid_message":
        _messages(p, [None])
    elif damage == "wrong_call_identity":
        _write(p / "call-one.json", {"call": {"id": "wrong", "messages": [{}]}})
    elif damage == "missing_call_id":
        result["rows"][0]["call_id"] = None
    elif damage == "missing_run_id":
        run.pop("id")
    elif damage == "missing_run_call_id":
        run.pop("callId")
    elif damage == "missing_join":
        iteration["runs"] = []
        iteration["runsTotal"] = 0
    elif damage == "duplicate_join":
        iteration["runs"].append(dict(run, id="run-two"))
        iteration["runsTotal"] = 2
    elif damage == "duplicate_result":
        result["rows"].append(dict(result["rows"][0]))
        result["total"] = 2
    elif damage == "unmatched_run":
        iteration["runs"].append(dict(run, id="run-two", callId="extra", outcome="error"))
        iteration["runsTotal"] = 2
    elif damage == "run_count":
        iteration["runsTotal"] = 2
    elif damage == "result_count":
        result["total"] = 2
    elif damage == "passed_count":
        result["passed"] = 0
    elif damage == "missing_verdict":
        run.pop("outcome")
    elif damage == "running_verdict":
        run["outcome"] = "running"
    elif damage == "metadata_mismatch":
        result["rows"][0]["simulation_outcome"] = "error"
    elif damage == "metadata_count":
        result["rows"][0]["simulation_match_count"] = 2
    elif damage == "metadata_join_error":
        result["rows"][0]["simulation_join_error"] = "unmatched_persona"
    _write(p / "results.json", result)
    if damage not in ("missing_iteration", "invalid_iteration"):
        _write(p / "iteration.json", iteration)
    report = audit_runtime(tmp_path)
    assert report["usable_as_clean_model_comparison"] is False
    assert report["incomplete_evidence_cases"] == ["test"]


def test_matching_collector_metadata_and_official_quality_failure_are_clean(tmp_path) -> None:
    p, result, iteration = _fixture(tmp_path, passed=False)
    run = iteration["runs"][0]
    result["rows"][0].update(
        simulation_outcome=run["outcome"],
        simulation_reason=run["reason"],
        simulation_run_id=run["id"],
        simulation_match_count=1,
    )
    _write(p / "results.json", result)
    assert audit_runtime(tmp_path)["usable_as_clean_model_comparison"] is True


def test_sidecar_is_exclusive_and_preserves_original_health_and_trace(tmp_path) -> None:
    p, _, _ = _fixture(tmp_path)
    health = p / "runtime-health.json"
    trace = tmp_path / "trace.jsonl"
    health.write_text("original health\n")
    trace.write_text("original trace\n")
    sidecar = tmp_path / "reaudit.json"
    audit_runtime(tmp_path, report_path=sidecar)
    assert health.read_text() == "original health\n"
    assert trace.read_text() == "original trace\n"
    assert json.loads(sidecar.read_text())["usable_as_clean_model_comparison"] is True
    with pytest.raises(FileExistsError, match="already exists"):
        audit_runtime(tmp_path, report_path=sidecar)


def test_no_cases_is_not_a_clean_comparison(tmp_path) -> None:
    p, _, _ = _fixture(tmp_path)
    _write(p / "results.json", {"passed": 0, "total": 0, "rows": []})
    _write(p / "iteration.json", {"runsTotal": 0, "runs": []})
    assert audit_runtime(tmp_path)["usable_as_clean_model_comparison"] is False


@pytest.mark.parametrize(
    ("evaluation", "case_id", "category"),
    [
        ("004-train-B", "DEV-TR-B-007", "unresolved_runtime_error_cases"),
        ("005-train-A", "DEV-TR-A-007", "simulator_error_cases"),
    ],
)
def test_real_training_records_reaudit_to_temp_without_rewriting_evidence(
    tmp_path, evaluation, case_id, category
) -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "runs/gepa-bounded-001/evaluations"
        / evaluation
        / "repeat-1/practice"
    )
    if not (source / "results.json").exists():
        pytest.skip("Local immutable training evidence is not distributed with source")
    files = list(source.glob("*.json"))
    original_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    practice = tmp_path / "practice"
    shutil.copytree(source, practice)
    original_result = json.loads((practice / "results.json").read_text())
    report = audit_runtime(tmp_path, report_path=tmp_path / "reaudit.json")
    assert case_id in report[category]
    assert report["usable_as_clean_model_comparison"] is False
    assert report["official_passed"] == original_result["passed"]
    assert report["official_total"] == original_result["total"]
    assert report["incomplete_evidence_cases"] == []
    if category == "unresolved_runtime_error_cases":
        case = next(c for c in report["cases"] if c["id"] == case_id)
        errors = case["unresolved_runtime_errors"]
        sandbox_errors = [e for e in errors if e["signature"] == UNRESOLVED_SIGNATURE]
        assert len(sandbox_errors) == 160
        assert sum(e["signature"] == EXTRACTION_ERROR_SIGNATURE for e in errors) == 11
        assert len(errors) == 171
        assert {e["function"] for e in sandbox_errors} == {
            "api_call",
            "code_exec",
        }
        assert "Stdout:" not in json.dumps(report)
    assert not (tmp_path / "trace.jsonl").exists()
    assert original_hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    assert original_hashes == {
        p.name: hashlib.sha256((practice / p.name).read_bytes()).hexdigest() for p in files
    }
