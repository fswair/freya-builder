"""Replay a known training date proposal under literal and normalized extraction."""

import argparse
import ast
import hashlib
import json
from copy import deepcopy
from pathlib import Path


def probe(workflow: Path, call: Path, event_index: int, complete_evidence: bool = False) -> dict:
    value = json.loads(workflow.read_text())
    code = value["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]
    tree = ast.parse(code)
    # This checks our recorded controller, not arbitrary untrusted Python.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name not in {"json", "datetime", "re"} for alias in node.names):
                raise ValueError("Unexpected controller import")
        elif isinstance(node, ast.ImportFrom):
            raise ValueError("Unexpected from import")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"open", "eval", "exec", "compile", "__import__", "input"}:
                raise ValueError("Unexpected dynamic execution or I/O")
    compiled = compile(tree, str(workflow), "exec")
    messages = json.loads(call.read_text())["call"]["messages"]
    event = json.loads(messages[event_index]["message"])
    if event.get("event") != "route_evaluated":
        raise ValueError("Use the flat variable table immediately before the controller")
    initial = event["variable_table"]
    before = json.loads(initial["call_state"])
    if before["stage"] != "negotiate" or not initial["turn_date"].startswith("today"):
        raise ValueError("Probe requires the recorded compound-today training proposal")
    expected_day = initial["now"].split("T")[0]
    evidence = {}
    if complete_evidence:
        latest_user = next(
            m["message"] for m in reversed(messages[:event_index]) if m["role"] == "user"
        )
        if initial.get("turn_raw") != latest_user:
            raise ValueError("Recorded raw evidence does not match the latest customer utterance")
        evidence = {
            "turn_consent_words": latest_user,
            "turn_coverage": "complete",
            "turn_missing": "none",
        }
    rows = []
    for name, day in (
        ("recorded_compound_date", initial["turn_date"]),
        ("normalized_iso_date", expected_day),
    ):
        variables = deepcopy(initial)
        variables.update(evidence)
        variables["turn_date"] = day
        exec(compiled, {"vars": variables})
        json.dumps(variables, allow_nan=False)
        state = json.loads(variables["call_state"])
        products = state["products"]
        if isinstance(products, dict):
            products = list(products.values())
        rows.append(
            {
                "name": name,
                "stage": state["stage"],
                "next": variables.get("next"),
                "prompt": state.get("prompt"),
                "products": [
                    {k: p.get(k) for k in ("id", "product_id", "amount", "date", "status")}
                    for p in products
                ],
                "expectation_met": state["stage"] == "confirm"
                and variables.get("next") != "create_promise"
                and any(p.get("date") == expected_day for p in products),
            }
        )
    return {
        "scope": (
            "Offline replay of a training state; assumes preserved A reference contract, "
            "not a Freya score"
        ),
        "workflow": str(workflow),
        "workflow_sha256": hashlib.sha256(workflow.read_bytes()).hexdigest(),
        "call": str(call),
        "event_index": event_index,
        "expected_date": expected_day,
        "synthetic_extraction_overrides": evidence,
        "qualification": "Complete evidence is a diagnostic assumption, not measured extraction.",
        "rows": rows,
        "all_expectations_met": all(row["expectation_met"] for row in rows),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("call", type=Path)
    parser.add_argument("--event-index", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--complete-evidence", action="store_true")
    args = parser.parse_args()
    result = probe(args.workflow, args.call, args.event_index, args.complete_evidence)
    with args.out.open("x") as output:
        output.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.out), "matched": result["all_expectations_met"]}))
