"""Recompile a recorded model adaptation with explicit reused-generation provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from freya_hackathon.adaptation import WorkflowAdaptation
from freya_hackathon.builder import utc_now, write_json
from freya_hackathon.compiler import compile_workflow
from freya_hackathon.optimization import source_fingerprint
from freya_hackathon.policy import Policy
from freya_hackathon.recording import capture_sync
from freya_hackathon.workflow import validate_workflow


def read(path: Path):
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recover(parent: Path, attempt: int, api_path: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    inputs = read(parent / "inputs.json")
    api_input = next(item for item in inputs if item["source"] == "tool-api")
    if digest(api_path) != api_input["sha256"]:
        raise ValueError("Supplied API differs from the recorded generation input")
    adaptation_path = parent / f"workflow-adaptation-{attempt}.json"
    adaptation = WorkflowAdaptation.model_validate(read(adaptation_path))
    references = read(parent / "workflow-references.json")
    draft = adaptation.apply(references)
    compiled, changes = compile_workflow(draft)
    policy = Policy.model_validate(read(parent / "policy.json"))
    validation = validate_workflow(compiled, policy, read(api_path))
    output.mkdir(parents=True)
    write_json(output / "workflow.json", compiled.workflow)
    write_json(output / "rule-nodes.json", compiled.rule_nodes)
    write_json(output / "policy.json", policy.model_dump())
    write_json(output / "validation.json", validation)
    write_json(output / "compiler-changes.json", changes)
    write_json(output / "inputs.json", inputs)
    write_json(output / "adaptation.json", adaptation.model_dump())
    write_json(output / "workflow-references.json", references)
    write_json(
        output / "generation-trace.json",
        [json.loads(line) for line in (parent / "trace.jsonl").read_text().splitlines()],
    )
    provenance = {
        "time": utc_now(),
        "event": "recorded_adaptation_recompiled",
        "parent": str(parent),
        "parent_trace_sha256": digest(parent / "trace.jsonl"),
        "adaptation_sha256": digest(adaptation_path),
        "reference_snapshot_sha256": digest(parent / "workflow-references.json"),
        "source_fingerprint": source_fingerprint(),
        "generation_reused": True,
        "independent_fresh_build": False,
        "model_output_manually_edited": False,
        "purpose": "Measure deterministic compiler correction on an unchanged recorded output",
        "changes": changes,
        "promoted": False,
    }
    write_json(output / "parent.json", provenance)
    (output / "trace.jsonl").write_text(json.dumps(provenance) + "\n")
    return {"output": str(output), "nodes": validation["node_count"], "changes": changes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent", type=Path)
    parser.add_argument("tool_api", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--attempt", type=int, default=2)
    args = parser.parse_args()
    result = capture_sync(
        args.output.resolve(),
        "adaptation_recovery",
        lambda: recover(
            args.parent.resolve(), args.attempt, args.tool_api.resolve(), args.output.resolve()
        ),
        inputs={"parent": str(args.parent), "attempt": args.attempt, "generation_reused": True},
    )
    print(json.dumps(result))
