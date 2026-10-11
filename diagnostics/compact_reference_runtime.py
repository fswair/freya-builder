"""Measure source-only sandbox budget savings and replay real controller inputs."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.compiler import compile_workflow
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync
from freya_hackathon.workflow import WorkflowBuild


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--calls", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def action():
        original = json.loads(args.workflow.read_text())
        compiled, changes = compile_workflow(
            WorkflowBuild(workflow=original, rule_nodes=json.loads(args.rules.read_text()))
        )
        assert all(c["fix"] == "compact_code_exec_layout" for c in changes), changes
        fields = ("function", "parameters", "properties", "code", "default")

        def controller(workflow):
            value = workflow["nodes"]["decide"]["pre_actions"][0]
            for name in fields:
                value = value[name]
            return value

        old, new = controller(original), controller(compiled.workflow)
        assert ast.dump(ast.parse(old)) == ast.dump(ast.parse(new))
        again, repeated_changes = compile_workflow(compiled)
        assert again == compiled and not repeated_changes
        write_json(args.out / "workflow.json", compiled.workflow)
        rows, sources = [], []
        for call in args.calls:
            sources.append(
                {"path": str(call), "sha256": hashlib.sha256(call.read_bytes()).hexdigest()}
            )
            for index, message in enumerate(json.loads(call.read_text())["call"]["messages"]):
                try:
                    event = json.loads(message.get("message", ""))
                except (ValueError, TypeError):
                    continue
                if not isinstance(event, dict) or event.get("event") != "route_evaluated":
                    continue
                if event.get("target") != "decide":
                    continue
                values = event["variable_table"]
                before = execute_python(old, {"vars": copy.deepcopy(values)})["vars"]
                after = execute_python(new, {"vars": copy.deepcopy(values)})["vars"]
                rows.append(
                    {"call": call.name, "event_index": index, "identical_output": before == after}
                )
        report = {
            "source": str(args.workflow),
            "source_sha256": hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            "workflow_sha256": hashlib.sha256(
                (args.out / "workflow.json").read_bytes()
            ).hexdigest(),
            "old_code_bytes": len(old.encode()),
            "new_code_bytes": len(new.encode()),
            "saved_code_bytes": len(old.encode()) - len(new.encode()),
            "identical_AST": True,
            "idempotent_compilation": True,
            "passed": sum(r["identical_output"] for r in rows),
            "total": len(rows),
            "rows": rows,
            "calls": sources,
            "compiler_changes": changes,
            "native_simulation": False,
            "fresh_generation": False,
            "promoted": False,
            "limits": "Local replay does not emulate the server's complete input envelope. "
            "Source savings reduce pressure but cannot guarantee all long calls fit.",
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "source_size_repair", action)
    print(json.dumps({k: report[k] for k in ["saved_code_bytes", "passed", "total"]}))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
