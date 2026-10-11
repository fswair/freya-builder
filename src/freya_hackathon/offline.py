"""Replay reviewed workflows with scripted extractions and bank responses, without network I/O.

This implements a documented subset of Freya, not its model, runtime or official judge.
Unsupported constructs fail explicitly. Only run code artifacts you have reviewed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from freya_hackathon.workflow import constant


def execute_python(code: str, values: dict[str, Any]) -> dict[str, Any]:
    worker = Path(__file__).with_name("_offline_worker.py")
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", str(worker)],
            input=json.dumps({"code": code, "values": values}),
            text=True,
            capture_output=True,
            timeout=4,
            check=False,
            env={},
        )
    except subprocess.TimeoutExpired:
        raise ValueError("Offline snippet exceeded its wall time limit") from None
    if completed.returncode:
        raise ValueError(f"Offline worker stopped (exit {completed.returncode})")
    result = json.loads(completed.stdout)
    if "error" in result:
        raise ValueError(result["error"])
    # Freya persists a merge, not replacement: absent keys stay in the variable table.
    result["values"]["vars"] = {**values["vars"], **result["values"]["vars"]}
    return result["values"]


def lookup(value: Any, path: str) -> Any:
    for key in path.split("."):
        if isinstance(value, str):
            value = json.loads(value)
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def matches(condition: dict, variables: dict) -> bool:
    if "conditions" in condition:
        children = [matches(c, variables) for c in condition["conditions"]]
        if condition.get("logic") not in {"and", "or"}:
            raise ValueError("Unsupported routing logic")
        return all(children) if condition["logic"] == "and" else any(children)
    operator = condition.get("operator")
    if operator == "always":
        return True
    value = variables.get(condition.get("variable"))
    operand = condition.get("operand")
    if operator == "equals":
        return value == operand
    if operator == "not_equals":
        return value != operand
    raise ValueError(f"Unsupported offline routing operator: {operator}")


def render(schema: dict, variables: dict) -> Any:
    if schema.get("type") == "object":
        return {key: render(child, variables) for key, child in schema["properties"].items()}
    value = constant(schema)
    if value is None:
        raise ValueError("Offline API actions require fixed/template parameter defaults")
    if isinstance(value, str):
        exact = re.fullmatch(r"\{\{\s*(\w+)\s*\}\}", value)
        if exact:
            value = variables[exact[1]]
        else:
            value = re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(variables[m[1]]), value)
    if schema.get("type") in {"number", "integer"}:
        value = float(value) if schema["type"] == "number" else int(value)
    return value


class Replay:
    def __init__(self, workflow: dict, variables: dict, responses: list[dict]):
        self.nodes = {**workflow["nodes"], **workflow.get("global_nodes", {})}
        self.node = workflow["initial_node"]
        self.variables = deepcopy(variables)
        self.responses = deepcopy(responses)
        self.calls: list[dict] = []
        self.visited: list[str] = []

    def transition(self, node: dict) -> str:
        for transition in node.get("transitions", []):
            if matches(transition["condition"], self.variables):
                return transition["target_node"]
        target = node.get("passthrough_default_transition")
        if not target:
            raise ValueError("No matching deterministic transition")
        return target

    def action(self, action: dict) -> dict:
        function = action["function"]
        handler = function["handler"]
        properties = function["parameters"]["properties"]
        if handler == "__function__:code_exec":
            code = constant(properties["code"])
            self.variables = execute_python(code, {"vars": self.variables})["vars"]
            return {}
        if handler != "__function__:api_call":
            raise ValueError(f"Unsupported offline action: {handler}")
        args = render(function["parameters"], self.variables)
        if action.get("pre_process_code"):
            changed = execute_python(
                action["pre_process_code"], {"vars": self.variables, "config": args}
            )
            self.variables, args = changed["vars"], changed["config"]
        operation = args["url"].rstrip("/").rsplit("/", 1)[-1]
        if self.calls and self.calls[-1]["operation"] == "close_call":
            raise ValueError("Bank operation after close_call")
        self.calls.append({"operation": operation, "body": args.get("body", {})})
        if not self.responses:
            raise ValueError(f"Unexpected bank operation: {operation}")
        response = self.responses.pop(0)
        if response["operation"] != operation:
            raise ValueError(f"Expected {response['operation']}, got {operation}")
        for path, expected in response.get("expect_body", {}).items():
            actual = lookup(args["body"], path)
            if actual != expected:
                raise ValueError(f"{operation} body {path}: expected {expected!r}, got {actual!r}")
        for path, expected in response.get("expect_optional_body", {}).items():
            try:
                actual = lookup(args["body"], path)
            except KeyError:
                continue
            if actual != expected:
                raise ValueError(f"{operation} optional body {path}: unexpected value {actual!r}")
        result = deepcopy(response["result"])
        if action.get("post_process_code"):
            changed = execute_python(
                action["post_process_code"], {"vars": self.variables, "result": result}
            )
            self.variables, result = changed["vars"], changed["result"]
        return result

    def advance(self) -> None:
        for _ in range(40):
            node = self.nodes[self.node]
            self.visited.append(self.node)
            if node["node_type"] == "end_call":
                return
            if node["node_type"] == "conversation" and not node.get("passthrough"):
                return
            result = {}
            for action in node.get("pre_actions", []):
                result = self.action(action)
            for mapping in node.get("response_mapping", []):
                self.variables[mapping["variable"]] = lookup(result, mapping["path"])
            self.node = self.transition(node)
        raise ValueError("Offline workflow exceeded 40 transitions without yielding")

    def turn(self, extracted: dict) -> None:
        node = self.nodes[self.node]
        if node["node_type"] != "conversation" or node.get("passthrough"):
            raise ValueError("Scripted customer turn requires a listening conversation node")
        self.variables.update(extracted)
        self.node = self.transition(node)
        self.advance()


def run_suite(workflow: dict, suite: dict) -> dict:
    if not suite.get("cases"):
        raise ValueError("Offline suite must contain at least one case")
    rows: list[dict[str, Any]] = []
    for case in suite["cases"]:
        replay = Replay(workflow, case["variables"], case.get("responses", []))
        replay.node = case.get("start_node", replay.node)
        try:
            replay.advance()
            for step in case.get("turns", []):
                replay.turn(step["extracted"])
                check(replay, step.get("expect", {}))
            check(replay, case.get("expect", {}))
            if replay.responses:
                raise ValueError("Expected bank operations were not made")
            error = None
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            error = str(exc)
        rows.append(
            {
                "id": case["id"],
                "passed": error is None,
                "error": error,
                "calls": replay.calls,
                "visited": replay.visited,
            }
        )
    return {
        "passed": sum(row["passed"] for row in rows),
        "total": len(rows),
        "scope": "Scripted extraction and fake bank responses; deterministic workflow subset only",
        "freya_evaluation": "not_run",
        "extraction_accuracy": "not_measured",
        "rows": rows,
    }


def check(replay: Replay, expected: dict) -> None:
    if "node" in expected and replay.node != expected["node"]:
        raise ValueError(f"Expected node {expected['node']}, got {replay.node}")
    for path, value in expected.get("variables", {}).items():
        actual = lookup(replay.variables, path)
        if actual != value:
            raise ValueError(f"{path}: expected {value!r}, got {actual!r}")
    if "operations" in expected:
        actual = [call["operation"] for call in replay.calls]
        if actual != expected["operations"]:
            raise ValueError(f"Expected operations {expected['operations']}, got {actual}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("suite", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    workflow_bytes, suite_bytes = args.workflow.read_bytes(), args.suite.read_bytes()
    report = run_suite(json.loads(workflow_bytes), json.loads(suite_bytes))
    report["workflow_sha256"] = hashlib.sha256(workflow_bytes).hexdigest()
    report["suite_sha256"] = hashlib.sha256(suite_bytes).hexdigest()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"passed": report["passed"], "total": report["total"], "out": str(args.out)}))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
