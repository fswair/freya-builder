"""Audit practice evidence completeness and runtime errors without changing official scores."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from freya_hackathon.builder import utc_now, write_json

INFRASTRUCTURE_SIGNATURE = "Failed to reach snekbox"
UNRESOLVED_SIGNATURE = "Sandbox did not return a parseable result"
SANDBOX_LIMIT_SIGNATURE = "Code and data for code_exec total "
PYTHON_TRACEBACK_SIGNATURE = "Traceback (most recent call last):"
MISSING_ANALYSIS_SIGNATURE = "Call ended but analysis never arrived"
BANK_WORKER_SIGNATURE = "Worker threw exception"
EXTRACTION_ERROR_SIGNATURE = "variable_extraction_error"
API_STATUS_ERROR_SIGNATURE = "api_call_error_status"
API_RESULT_ERROR_SIGNATURE = "api_call_error_result"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _read_object(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _runtime_errors(messages: list) -> tuple[list, list]:
    infrastructure, unresolved = [], []
    pending_api_nodes = set()
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        try:
            event = json.loads(message.get("message", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind, node = event.get("event"), message.get("nodeId")
        function = event.get("function")
        structured_signature = None
        if kind == "function_call_start" and function == "api_call" and isinstance(node, str):
            pending_api_nodes.add(node)
            continue
        if kind == "extraction_error":
            structured_signature = EXTRACTION_ERROR_SIGNATURE
        elif kind == "function_call_complete" and function == "api_call":
            if isinstance(node, str):
                pending_api_nodes.discard(node)
            result = event.get("result")
            if isinstance(result, dict) and result.get("status") == "error":
                structured_signature = API_RESULT_ERROR_SIGNATURE
        elif kind == "route_evaluated" and isinstance(node, str) and node in pending_api_nodes:
            # A timeout can omit function_call_error/complete entirely. Only read
            # response metadata on the same node after an actual api_call start;
            # later controllers may carry stale copies of this variable table.
            pending_api_nodes.remove(node)
            values = event.get("variable_table")
            if isinstance(values, dict):
                status = values.get(f"{node}_response.status")
                status_code = values.get(f"{node}_response.status_code")
                if status == "error" and type(status_code) is int and 400 <= status_code <= 599:
                    structured_signature = API_STATUS_ERROR_SIGNATURE
        if structured_signature:
            unresolved.append(
                {
                    "event_index": index,
                    "node": node,
                    "seconds": message.get("secondsFromStart"),
                    "function": "variable_extraction" if kind == "extraction_error" else "api_call",
                    "signature": structured_signature,
                    "error_sha256": _digest(json.dumps(event, sort_keys=True)),
                }
            )
            continue
        # Ignore copied errors in snapshots and business failures in successful
        # HTTP results. They are not standalone execution failure events.
        if kind != "function_call_error":
            continue
        if function == "api_call" and isinstance(node, str):
            pending_api_nodes.discard(node)
        error = event.get("error")
        if not isinstance(error, str):
            continue
        if error.startswith(INFRASTRUCTURE_SIGNATURE):
            target, signature = infrastructure, INFRASTRUCTURE_SIGNATURE
        elif error.startswith(UNRESOLVED_SIGNATURE):
            target, signature = unresolved, UNRESOLVED_SIGNATURE
        elif error.startswith(SANDBOX_LIMIT_SIGNATURE) and (
            "over the 131072-byte limit a sandboxed process can receive" in error
        ):
            target, signature = unresolved, SANDBOX_LIMIT_SIGNATURE
        elif event.get("function") == "code_exec" and error.startswith(PYTHON_TRACEBACK_SIGNATURE):
            target, signature = unresolved, PYTHON_TRACEBACK_SIGNATURE
        elif (
            event.get("function") == "api_call"
            and error.startswith("API request failed:")
            and BANK_WORKER_SIGNATURE in error
        ):
            # Fault injection versus service fault is not established by this event.
            target, signature = unresolved, BANK_WORKER_SIGNATURE
        else:
            continue
        target.append(
            {
                "event_index": index,
                "node": message.get("nodeId"),
                "seconds": message.get("secondsFromStart"),
                "function": event.get("function"),
                "signature": signature,
                # Sandbox stdout can contain the entire variable table. Never duplicate it.
                "error_sha256": _digest(error),
            }
        )
    return infrastructure, unresolved


def _iteration_evidence(directory: Path, rows: list) -> tuple[dict, list[str]]:
    iteration = _read_object(directory / "iteration.json")
    if iteration is None:
        return {}, ["iteration_missing_or_invalid"]
    runs = iteration.get("runs")
    if not isinstance(runs, list):
        return {}, ["iteration_missing_or_invalid"]
    issues = []
    if type(iteration.get("runsTotal")) is not int or iteration["runsTotal"] != len(runs):
        issues.append("iteration_run_count_mismatch")
    by_call = defaultdict(list)
    run_ids = []
    requested_calls = {r.get("call_id") for r in rows}
    for run in runs:
        if not isinstance(run, dict):
            issues.append("iteration_run_invalid")
            continue
        run_id, call_id = run.get("id"), run.get("callId")
        start, end = run.get("startedAt"), run.get("endedAt")
        if isinstance(start, str) and isinstance(end, str):
            try:
                if datetime.fromisoformat(end) < datetime.fromisoformat(start):
                    issues.append("iteration_run_ended_before_started")
            except (TypeError, ValueError):
                issues.append("iteration_run_times_invalid")
        if not isinstance(run_id, str) or not run_id:
            issues.append("iteration_run_id_missing")
        else:
            run_ids.append(run_id)
        if not isinstance(call_id, str) or not call_id:
            issues.append("iteration_call_id_missing")
            continue
        by_call[call_id].append(run)
        if call_id not in requested_calls:
            issues.append("iteration_run_unmatched")
    if len(run_ids) != len(set(run_ids)):
        issues.append("iteration_run_id_duplicate")
    return dict(by_call), sorted(set(issues))


def audit_runtime(run: Path, *, report_path: Path | None = None) -> dict:
    """Default writes legacy health/trace; an explicit new path writes only that sidecar."""
    if report_path is not None and report_path.exists():
        raise FileExistsError(f"Audit sidecar already exists: {report_path}")
    directory = run / "practice"
    results = _read_object(directory / "results.json")
    if results is None or not isinstance(results.get("rows"), list):
        raise ValueError("Practice results are missing or invalid")
    rows = results["rows"]
    if any(
        not isinstance(row, dict)
        or not isinstance(row.get("id"), str)
        or not row["id"]
        or (row.get("call_id") is not None and not isinstance(row["call_id"], str))
        for row in rows
    ):
        raise ValueError("Practice result row identity is invalid")
    by_call, global_issues = _iteration_evidence(directory, rows)
    if not rows:
        global_issues.append("no_result_rows")
    if type(results.get("total")) is not int or results["total"] != len(rows):
        global_issues.append("result_count_mismatch")
    if type(results.get("passed")) is not int or results["passed"] != sum(
        row.get("pass") is True for row in rows
    ):
        global_issues.append("result_pass_count_mismatch")
    case_counts = Counter(row["id"] for row in rows)
    call_counts = Counter(row.get("call_id") for row in rows)
    cases = []
    for row in rows:
        case_id, call_id = row["id"], row.get("call_id")
        incomplete = list(global_issues)
        case = {
            "id": case_id,
            "transcript_available": False,
            "official_pass": row.get("pass"),
            "infrastructure_errors": [],
            "unresolved_runtime_errors": [],
            "simulator_errors": [],
        }
        if type(row.get("pass")) is not bool:
            incomplete.append("official_verdict_missing_or_invalid")
        if case_counts[case_id] != 1:
            incomplete.append("result_case_id_duplicate")
        if call_id and call_counts[call_id] != 1:
            incomplete.append("result_call_id_duplicate")
        if row.get("simulation_join_error") or (
            "simulation_match_count" in row
            and (
                type(row["simulation_match_count"]) is not int or row["simulation_match_count"] != 1
            )
        ):
            incomplete.append("collector_join_invalid")
        matches = by_call.get(call_id, [])
        if len(matches) != 1:
            incomplete.append(
                "iteration_join_missing" if not matches else "iteration_join_ambiguous"
            )
        else:
            simulation = matches[0]
            outcome = simulation.get("outcome")
            reason = simulation.get("reason")
            for field, key in (
                ("simulation_outcome", "outcome"),
                ("simulation_reason", "reason"),
                ("simulation_run_id", "id"),
            ):
                if field in row and row[field] != simulation.get(key):
                    incomplete.append("result_simulation_metadata_mismatch")
            case["simulation_outcome"] = outcome if outcome in ("pass", "fail", "error") else None
            missing_analysis = isinstance(reason, str) and reason.startswith(
                MISSING_ANALYSIS_SIGNATURE
            )
            if outcome == "error" or missing_analysis:
                case["simulator_errors"].append(
                    {
                        "run_id": simulation.get("id"),
                        "signature": MISSING_ANALYSIS_SIGNATURE
                        if missing_analysis
                        else "terminal_simulator_error",
                        "reason_sha256": _digest(reason) if isinstance(reason, str) else None,
                    }
                )
            elif outcome not in ("pass", "fail"):
                incomplete.append("simulation_verdict_not_terminal")
        if not call_id or Path(call_id).name != call_id:
            incomplete.append("call_id_missing_or_invalid")
        else:
            record = _read_object(directory / f"call-{call_id}.json")
            call = record.get("call") if record else None
            messages = call.get("messages") if isinstance(call, dict) else None
            if not isinstance(call, dict) or not isinstance(messages, list) or not messages:
                incomplete.append("transcript_missing_or_invalid")
            else:
                case["transcript_available"] = True
                if call.get("id") not in (None, call_id):
                    incomplete.append("transcript_call_id_mismatch")
                if any(
                    not isinstance(message, dict)
                    or not isinstance(message.get("role"), str)
                    or not isinstance(message.get("message"), str)
                    for message in messages
                ):
                    incomplete.append("transcript_message_invalid")
                infrastructure, unresolved = _runtime_errors(messages)
                case["infrastructure_errors"] = infrastructure
                case["unresolved_runtime_errors"] = unresolved
        case["incomplete_evidence"] = sorted(set(incomplete))
        cases.append(case)
    categories = {
        name: list(dict.fromkeys(case["id"] for case in cases if case[field]))
        for name, field in (
            ("infrastructure_affected_cases", "infrastructure_errors"),
            ("unresolved_runtime_error_cases", "unresolved_runtime_errors"),
            ("simulator_error_cases", "simulator_errors"),
            ("incomplete_evidence_cases", "incomplete_evidence"),
        )
    }
    without_snekbox = [
        c for c in cases if c["transcript_available"] and not c["infrastructure_errors"]
    ]
    report = {
        "official_passed": results.get("passed"),
        "official_total": results.get("total"),
        **categories,
        "usable_as_clean_model_comparison": bool(cases)
        and not global_issues
        and not any(categories.values()),
        "evidence_issues": sorted(set(global_issues)),
        "without_observed_snekbox_error": {
            "passed": sum(c["official_pass"] is True for c in without_snekbox),
            "total": len(without_snekbox),
        },
        "interpretation": "Official score is unchanged. Error presence does not establish cause. "
        "Sandbox parse failures have unresolved provenance, not proven infrastructure fault. "
        "Terminal simulator errors and incomplete evidence block clean comparison. "
        "The subset without observed Snekbox errors is not an adjusted score or clean subset.",
        "cases": cases,
    }
    if report_path is not None:
        # Exclusive sidecars preserve historical health reports and native run traces.
        with report_path.open("x", encoding="utf-8") as output:
            output.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    else:
        write_json(directory / "runtime-health.json", report)
        with (run / "trace.jsonl").open("a") as output:
            output.write(
                json.dumps({"time": utc_now(), "event": "runtime_health_audit", **categories})
                + "\n"
            )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument(
        "--report-path", type=Path, help="Write a new sidecar without editing the run"
    )
    args = parser.parse_args()
    result = audit_runtime(args.run, report_path=args.report_path)
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, indent=2))


if __name__ == "__main__":
    main()
