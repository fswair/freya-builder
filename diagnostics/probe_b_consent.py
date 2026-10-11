"""Replay development consent mutations in the recorded B controller, without I/O."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from copy import deepcopy
from pathlib import Path


def probe(workflow: Path, call: Path, event_index: int, *, recorded_turn: bool = False) -> dict:
    value = json.loads(workflow.read_text())
    code = value["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]
    tree = ast.parse(code)
    # This is an offline diagnostic for our recorded controller, not a general code sandbox.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name not in {"json", "datetime", "re"} for alias in node.names):
                raise ValueError("Unexpected import in recorded controller")
        elif isinstance(node, ast.ImportFrom):
            raise ValueError("Unexpected from import in recorded controller")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"open", "eval", "exec", "compile", "__import__", "input"}:
                raise ValueError("Unexpected dynamic execution or I/O in controller")
    compiled = compile(tree, str(workflow), "exec")
    messages = json.loads(call.read_text())["call"]["messages"]
    event = json.loads(messages[event_index]["message"])
    initial = event["variable_table"]
    if recorded_turn:
        state = deepcopy(initial)
        exec(compiled, {"vars": state})
        json.dumps(state, allow_nan=False)

        def products(variables: dict) -> list[dict]:
            return [
                {key: product.get(key) for key in ("product_id", "status", "amount", "date")}
                for product in json.loads(variables["state"])["products"]
            ]

        return {
            "workflow": str(workflow),
            "workflow_sha256": hashlib.sha256(workflow.read_bytes()).hexdigest(),
            "call": str(call),
            "event_index": event_index,
            "scope": "local replay of one recorded turn; same state contract assumed",
            "before_products": products(initial),
            "after_products": products(state),
            "route": state.get("route"),
            "phase": state.get("phase"),
            "message": state.get("message"),
        }
    if json.loads(initial["state"])["phase"] != "confirm":
        raise ValueError("Probe requires a recorded complete confirmation readback")
    variants = {
        "missing_utterance_accept": {"turn_raw": "", "turn_decision": "accept"},
        "meta_description_accept": {
            "turn_raw": "The agent is summarizing the payment plan",
            "turn_decision": "accept",
        },
        "short_yes": {"turn_raw": "Yes", "turn_decision": "accept"},
        "process_question": {
            "turn_raw": "What happens if I agree?",
            "turn_decision": "none",
            "turn_question": "What happens if I agree?",
        },
        "no": {"turn_raw": "No", "turn_decision": "reject"},
    }
    rows = []
    for name, changes in variants.items():
        state = deepcopy(initial)
        for key in state:
            if key.startswith("turn_"):
                state[key] = ""
        state.update(changes)
        namespace = {"vars": state}
        exec(compiled, namespace)  # Only local state mutation; no bank/API execution.
        json.dumps(state, allow_nan=False)
        rows.append(
            {
                "name": name,
                "route": state.get("route"),
                "phase": state.get("phase"),
                "message": state.get("message"),
                "write_requested": state.get("route") == "create_promise",
                "expected_write_requested": name == "short_yes",
            }
        )
    return {
        "workflow": str(workflow),
        "workflow_sha256": hashlib.sha256(workflow.read_bytes()).hexdigest(),
        "call": str(call),
        "event_index": event_index,
        "scope": "local synthetic mutations of a recorded confirmation state; not Freya scoring",
        "rows": rows,
        "all_expectations_met": all(
            row["write_requested"] == row["expected_write_requested"] for row in rows
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("call", type=Path)
    parser.add_argument("--event-index", type=int, required=True)
    parser.add_argument("--recorded-turn", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = probe(args.workflow, args.call, args.event_index, recorded_turn=args.recorded_turn)
    with args.out.open("x") as output:
        output.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.out), "matched": report.get("all_expectations_met")}))
