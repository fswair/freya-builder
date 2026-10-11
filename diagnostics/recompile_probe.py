"""Isolate compiler changes in an existing development build using fixed text cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from freya_hackathon.builder import ROOT, utc_now, write_json
from freya_hackathon.compiler import compile_workflow
from freya_hackathon.optimization import source_fingerprint, workspace_lock
from freya_hackathon.optimization_data import StudyConfig
from freya_hackathon.policy import Policy
from freya_hackathon.practice import Practice
from freya_hackathon.recording import capture_sync
from freya_hackathon.workflow import WorkflowBuild, validate_workflow


def read(path: Path):
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(source: Path, cases: Path, brief: str, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    inputs = StudyConfig.read(ROOT / "optimization.example.json").inputs()[brief]
    selected = read(cases)
    if not selected or any(case["brief"] != brief for case in selected):
        raise ValueError("Cases must all belong to the selected brief")
    draft = WorkflowBuild(
        workflow=read(source / "workflow.json"), rule_nodes=read(source / "rule-nodes.json")
    )
    compiled, changes = compile_workflow(draft)
    if not changes or any(change["fix"] != "permit_explicit_blank_reset" for change in changes):
        raise ValueError("This probe isolates extraction enum reset alignment only")
    policy = Policy.model_validate(read(source / "policy.json"))
    validation = validate_workflow(compiled, policy, inputs.api)
    output.mkdir(parents=True)
    for name in ("policy.json", "rule-nodes.json", "inputs.json"):
        shutil.copyfile(source / name, output / name)
    shutil.copyfile(cases, output / "cases.json")
    write_json(output / "workflow.json", compiled.workflow)
    write_json(output / "validation.json", validation)
    write_json(output / "compiler-changes.json", changes)
    manifest = {
        "started_at": utc_now(),
        "source_fingerprint": source_fingerprint(),
        "parent": str(source),
        "parent_workflow_sha256": digest(source / "workflow.json"),
        "workflow_sha256": digest(output / "workflow.json"),
        "parent_trace_sha256": digest(source / "trace.jsonl"),
        "cases_sha256": digest(cases),
        "brief": brief,
        "holdout_opened": False,
        "generation_reused": True,
        "purpose": "Isolate accepted enum reset semantics on the same development cases",
        "limits": "One stochastic repetition; not a fresh timed build or submission candidate",
    }
    write_json(output / "parent.json", manifest)
    practice = Practice(output, 900)
    try:
        report = practice.execute(output / "cases.json", brief, f"reset-probe-{brief}")
    finally:
        practice.client.close()
    health = read(output / "practice/runtime-health.json")
    summary = {
        "ended_at": utc_now(),
        "passed": report["passed"],
        "total": report["total"],
        "failed": [row["id"] for row in report["rows"] if not row["pass"]],
        "runtime_health": health,
        "promoted": False,
    }
    write_json(output / "report.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("cases", type=Path)
    parser.add_argument("brief", choices=["A", "B"])
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with workspace_lock():
        result = capture_sync(
            args.output.resolve(),
            "recompile_probe",
            lambda: run(
                args.source.resolve(), args.cases.resolve(), args.brief, args.output.resolve()
            ),
            inputs={"parent": str(args.source), "generation_reused": True},
        )
    print(
        json.dumps(
            {"output": str(args.output), "passed": result["passed"], "total": result["total"]}
        )
    )
