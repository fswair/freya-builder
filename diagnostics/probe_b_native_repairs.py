"""Replay actual failed public/train controller inputs before and after repair."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync

ROOT = Path(__file__).resolve().parents[1]
CALLS = {
    "aggregate": ("v12-b-003-call-read-001", "24cc8d45-1a3e-4061-b915-eb62a787ca9f"),
    "ordinal": ("v12-b-008-call-read-001", "452e4137-9726-4fa1-b628-1cea80358405"),
    "stale": ("v12-b-pub13-call-read-001", "afa9e6c2-3958-4c32-9c12-2b48f7101b2e"),
    "paid_dispute": ("v12-b-005-call-read-001", "d90d7fb0-a330-49ca-a396-a795d26c0dbc"),
}


def seed(kind):
    directory, call_id = CALLS[kind]
    path = ROOT / "diagnostics" / directory / "practice" / f"call-{call_id}.json"
    messages = json.loads(path.read_text())["call"]["messages"]
    for message in messages:
        try:
            event = json.loads(message["message"])
        except (ValueError, TypeError):
            continue
        if event.get("event") != "route_evaluated":
            continue
        values = event.get("variable_table", {})
        raw = values.get("turn_raw", "")
        match = (
            (
                kind == "aggregate"
                and event.get("from_node") == "dialogue"
                and raw.startswith("I cannot pay all today")
            )
            or (
                kind == "ordinal"
                and event.get("from_node") == "dialogue"
                and raw.startswith("Keep the first two")
            )
            or (
                kind == "stale"
                and event.get("from_node") == "create_promise"
                and values.get("create_promise_response.status") == "error"
            )
            or (
                kind == "paid_dispute"
                and event.get("from_node") == "dialogue"
                and raw.startswith("The card’s overdue amount was paid")
            )
        )
        if match:
            return values, {
                "source": str(path.relative_to(ROOT)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "seconds": message["secondsFromStart"],
                "call_id": call_id,
            }
    raise ValueError(f"Native input not found: {kind}")


def replay(workflow, values, *, api_error=False, with_values=False):
    values = copy.deepcopy(values)
    if api_error:
        status = values["create_promise_response.status"]
        code = workflow["nodes"]["create_promise"]["pre_actions"][0]["pre_process_code"]
        stale_payload = values["api_data"]
        values = execute_python(code, {"vars": values})["vars"]
        # Preserve the actual post-request status and retained mapping from the trace.
        values["create_promise_response.status"] = status
        values["api_data"] = stale_payload
    code = workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]
    result = execute_python(code, {"vars": values})["vars"]
    state = json.loads(result["state"])
    summary = {
        "phase": state.get("phase"),
        "route": result.get("route"),
        "message": result.get("message"),
        "products": [
            {
                key: product.get(key)
                for key in (
                    "product_id",
                    "status",
                    "reason",
                    "amount",
                    "date",
                    "confirmed",
                    "promise_id",
                    "create_tries",
                )
            }
            for product in state.get("products", [])
        ],
        "pending_count": len(state.get("pending_clauses", [])),
        "request": {
            key: result.get("request_" + key) for key in ("product_id", "amount", "date", "reason")
        },
    }
    return (summary, result) if with_values else summary


def acceptable(kind, result):
    if kind == "aggregate":
        return result["phase"] == "negotiate" and result["route"] == "dialogue"
    if kind == "ordinal":
        return (
            result["phase"] == "confirm"
            and result["route"] == "dialogue"
            and [product["date"] for product in result["products"]]
            == ["2026-10-16", "2026-10-16", "2026-10-15"]
            and not any(product["confirmed"] for product in result["products"])
        )
    if kind == "paid_dispute":
        return (
            result["route"] == "add_note"
            and [product["reason"] for product in result["products"]] == ["paid", "dispute"]
            and all(product["status"] == "excluded" for product in result["products"])
            and result["pending_count"] == 0
        )
    return (
        result["products"][0]["status"] == "recorded"
        and result["products"][1]["status"] != "recorded"
        and result["route"] == "create_promise"
        and result["products"][1]["create_tries"] == 2
        and result["request"]["product_id"] == "p2"
        and result["request"]["amount"] == 900
        and result["request"]["date"] == "2026-10-23"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def evaluate():
        workflows = {
            key: json.loads(path.read_text())
            for key, path in (("baseline", args.baseline), ("candidate", args.candidate))
        }
        rows = []
        for kind in CALLS:
            values, provenance = seed(kind)
            results = {
                key: replay(workflow, values, api_error=kind == "stale")
                for key, workflow in workflows.items()
            }
            rows.append(
                {
                    "case": kind,
                    "provenance": provenance,
                    "results": results,
                    "baseline_pass": acceptable(kind, results["baseline"]),
                    "candidate_pass": acceptable(kind, results["candidate"]),
                }
            )
        values, provenance = seed("stale")
        _, after_first = replay(workflows["candidate"], values, api_error=True, with_values=True)
        after_first["api_data"] = values["api_data"]
        after_second, after_second_values = replay(
            workflows["candidate"], after_first, api_error=True, with_values=True
        )
        after_second_values.update(
            {key: "" for key in after_second_values if key.startswith("turn_")}
        )
        after_second_values["turn_raw"] = "No, thank you."
        after_second_values["turn_decision"] = "reject"
        closing = replay(workflows["candidate"], after_second_values)
        bounded = (
            after_second["route"] != "create_promise"
            and closing["route"] == "add_note"
            and closing["request"]["reason"] == "service_error"
            and closing["request"]["product_id"] == "p2"
            and after_second["products"][0]["status"] == "recorded"
            and after_second["products"][1]["status"] == "excluded"
            and after_second["products"][1]["create_tries"] == 2
        )
        rows.append(
            {
                "case": "two_failed_writes_stop",
                "provenance": provenance,
                "results": {"candidate": after_second, "closing": closing},
                "candidate_pass": bounded,
            }
        )
        report = {
            "passed": sum(row["candidate_pass"] is True for row in rows),
            "total": len(rows),
            "cases": rows,
            "native_simulation": False,
            "promoted": False,
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "b_native_failure_replay", evaluate)
    print(json.dumps({"passed": report["passed"], "total": report["total"]}))


if __name__ == "__main__":
    main()
