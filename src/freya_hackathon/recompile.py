"""Replay a recorded candidate through the compiler for local runtime debugging."""

import argparse
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import utc_now, write_json
from freya_hackathon.compiler import compile_workflow
from freya_hackathon.policy import Policy
from freya_hackathon.workflow import WorkflowBuild, validate_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tool-api", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    source = WorkflowBuild(
        workflow=json.loads((args.source / "workflow.json").read_text()),
        rule_nodes=json.loads((args.source / "rule-nodes.json").read_text()),
    )
    policy = Policy.model_validate_json((args.source / "policy.json").read_text())
    draft, changes = compile_workflow(source)
    report = validate_workflow(draft, policy, json.loads(args.tool_api.read_text()))
    write_json(args.out / "workflow.json", draft.workflow)
    write_json(args.out / "policy.json", policy.model_dump())
    write_json(args.out / "rule-nodes.json", draft.rule_nodes)
    write_json(args.out / "validation.json", report)
    write_json(args.out / "compiler-changes.json", changes)
    (args.out / "parent-trace.jsonl").write_bytes((args.source / "trace.jsonl").read_bytes())
    event = {
        "time": utc_now(),
        "event": "debug_recompile",
        "source": str(args.source.resolve()),
        "source_sha256": hashlib.sha256((args.source / "workflow.json").read_bytes()).hexdigest(),
        "changes": changes,
        "new_model_calls": 0,
        "submission_ready": False,
    }
    (args.out / "trace.jsonl").write_text(json.dumps(event) + "\n")
    print(json.dumps({"output": str(args.out), "changes": changes}, indent=2))


if __name__ == "__main__":
    main()
