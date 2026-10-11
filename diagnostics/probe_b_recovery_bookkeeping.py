"""Replay B recovery through real node preparation, response mapping and terminal routing."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync


def matches(condition: dict, values: dict) -> bool:
    if "conditions" in condition:
        results = [matches(item, values) for item in condition["conditions"]]
        return all(results) if condition["logic"] == "and" else any(results)
    operator = condition["operator"]
    if operator == "always":
        return True
    value = values.get(condition["variable"])
    if operator == "equals":
        return value == condition["operand"]
    if operator == "greater_than":
        return float(value or 0) > condition["operand"]
    raise ValueError(f"Unsupported probe condition: {operator}")


def next_node(node: dict, values: dict) -> str:
    return next(
        transition["target_node"]
        for transition in node["transitions"]
        if matches(transition["condition"], values)
    )


def response(node: dict, values: dict, payload: dict | None) -> dict:
    """Model bank response post-processing/mapping, including an unmapped transport failure."""
    # Native Freya reports transport status separately from payload.ok. The API
    # prehook now resets this status to pending, so the fake response must supply
    # the completed status just as the recorded native calls do.
    values[f"{node['name']}_response.status"] = "error" if payload is None else "success"
    if payload is None:
        return values
    action = node["pre_actions"][0]
    result = {"data": payload}
    code = action.get("post_process_code")
    if code:
        result = execute_python(code, {"vars": values, "result": result})["result"]
    for mapping in node.get("response_mapping", []):
        value = result
        for part in mapping["path"].split("."):
            value = value[part]
        values[mapping["variable"]] = value
    return values


def product(pid: str, status: str = "agreed", reason: str = "") -> dict:
    return {"product_id": pid, "status": status, "reason": reason}


def cases() -> list[dict]:
    return [
        {
            "id": "successful-second-write",
            "products": [product("p1", "recorded"), product("p2"), product("p3")],
            "pending": "create_promise",
            "affected": "p2",
            "ok": True,
            "notes": [["p3", "service_error"]],
            "outcome": "promise",
            "reason": None,
        },
        {
            "id": "failed-write-keeps-prior-promise",
            "products": [
                product("p1", "recorded"),
                product("p2"),
                product("p3", "excluded", "paid"),
            ],
            "pending": "create_promise",
            "affected": "p2",
            "ok": False,
            "notes": [["p2", "service_error"], ["p3", "paid"]],
            "outcome": "promise",
            "reason": None,
        },
        {
            "id": "no-promises-first-nonpaid-reason",
            "products": [
                product("p1", "excluded", "paid"),
                product("p2", "excluded", "refused"),
                product("p3"),
            ],
            "notes": [["p1", "paid"], ["p2", "refused"], ["p3", "service_error"]],
            "outcome": "no_promise",
            "reason": "refused",
        },
        {
            "id": "all-paid",
            "products": [product("p1", "excluded", "paid"), product("p2", "excluded", "paid")],
            "notes": [["p1", "paid"], ["p2", "paid"]],
            "outcome": "paid",
            "reason": None,
        },
        {
            "id": "accepted-transfer-overrides-promise",
            "products": [product("p1", "recorded"), product("p2")],
            "pending": "transfer_to_agent",
            "ok": True,
            "notes": [],
            "outcome": "transferred",
            "reason": "support",
        },
        {
            "id": "acknowledged-note-not-repeated",
            "products": [
                product("p1", "excluded", "refused"),
                product("p2", "excluded", "dispute"),
            ],
            "pending": "add_note",
            "affected": "p1",
            "ok": True,
            "notes": [["p2", "dispute"]],
            "outcome": "no_promise",
            "reason": "refused",
        },
        {
            "id": "failed-note-not-repeated",
            "products": [
                product("p1", "excluded", "refused"),
                product("p2", "excluded", "dispute"),
            ],
            "pending": "add_note",
            "affected": "p1",
            "ok": False,
            "notes": [["p2", "dispute"]],
            "outcome": "no_promise",
            "reason": "refused",
        },
        {
            "id": "earlier-completed-note-skipped",
            "products": [dict(product("p1", "excluded", "paid"), note_done=True), product("p2")],
            "notes": [["p2", "service_error"]],
            "outcome": "no_promise",
            "reason": "service_error",
        },
    ]


def replay(workflow: dict, case: dict, note_result: str) -> dict:
    nodes = workflow["nodes"]
    values = {
        "customer_id": "synthetic-recovery-probe",
        "state": json.dumps(
            {
                "verified": True,
                "mode": "B",
                "queue": "support",
                "requested_transfer_reason": "support",
                "products": copy.deepcopy(case["products"]),
            }
        ),
        "request_product_id": case.get("affected", ""),
    }
    pending = case.get("pending")
    if pending:
        action = nodes[pending]["pre_actions"][0]
        values = execute_python(action["pre_process_code"], {"vars": values})["vars"]
        values = response(nodes[pending], values, {"ok": case["ok"]})
    visited, notes = [], []
    current = "runtime_recovery"
    close_body = None
    for _ in range(16):
        visited.append(current)
        node = nodes[current]
        if current == "runtime_recovery":
            code = node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
            values = execute_python(code, {"vars": values})["vars"]
            values["runtime_recovery_response.status"] = "success"
            values["runtime_recovery_failure_count"] = 0
        elif current == "add_note":
            notes.append([values["request_product_id"], values["request_reason"]])
            action = node["pre_actions"][0]
            values = execute_python(action["pre_process_code"], {"vars": values})["vars"]
            payload = None if note_result == "transport-error" else {"ok": note_result == "success"}
            values = response(node, values, payload)
        elif current == "farewell":
            pass  # Speech quality is not measured by this offline routing probe.
        elif current == "close_call":
            action = node["pre_actions"][0]
            prepared = execute_python(
                action["pre_process_code"], {"vars": values, "config": {"body": {}}}
            )
            values, close_body = prepared["vars"], prepared["config"]["body"]
        elif node.get("terminal", {}).get("function", {}).get("handler") == "__function__:end_call":
            break
        else:
            raise AssertionError(f"Recovery reached forbidden node: {current}")
        current = next_node(node, values)
    else:
        raise AssertionError("Recovery failed to terminate within 16 nodes")
    assert notes == case["notes"], {"expected_notes": case["notes"], "actual_notes": notes}
    assert len({row[0] for row in notes}) == len(notes), "Duplicate product note"
    assert visited.count("close_call") == 1, visited
    assert close_body is not None
    actual = close_body["outcome"], close_body.get("reason")
    expected = case["outcome"], case["reason"]
    assert actual == expected, {"expected_close": expected, "actual_close": actual}
    return {"notes": notes, "visited": visited, "close": close_body}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    workflow = json.loads(args.workflow.read_text())

    def probe() -> dict:
        rows = []
        for case in cases():
            for note_result in ("success", "business-failure", "transport-error"):
                row = {"case": case["id"], "note_result": note_result}
                try:
                    row.update(replay(workflow, case, note_result), passed=True)
                except Exception as exc:
                    row.update(passed=False, error_type=type(exc).__name__, error=str(exc))
                rows.append(row)
        report = {
            "scope": "Synthetic local runtime recovery; no Freya request or native score.",
            "limits": (
                "Does not assess speech, sandbox availability, "
                "failed-transfer semantics or remote execution."
            ),
            "workflow": str(args.workflow),
            "sha256": hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            "passed": sum(row["passed"] is True for row in rows),
            "total": len(rows),
            "rows": rows,
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "offline_b_recovery_bookkeeping", probe)
    print(json.dumps({key: report[key] for key in ("passed", "total", "sha256")}))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
