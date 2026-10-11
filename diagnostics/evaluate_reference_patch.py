"""Measure an explicit development reference correction without claiming a fresh build."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from functools import partial
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


def run(
    parent: Path,
    workflow: Path,
    cases: Path,
    brief: str,
    output: Path,
    seconds: float = 900,
    *,
    read_retries: bool = False,
    concurrency: int = 5,
) -> dict:
    if output.exists():
        raise FileExistsError(output)
    if not 1 <= concurrency <= 5:
        raise ValueError("Diagnostic concurrency must be between one and five")
    inputs = StudyConfig.read(ROOT / "optimization.example.json").inputs()[brief]
    selected = read(cases)
    if not selected or any(case["brief"] != brief for case in selected):
        raise ValueError("Cases must belong to the selected brief")
    if any(not case["id"].startswith(("PUB-", "DEV-TR-")) for case in selected):
        raise ValueError("This correction probe accepts only public and training cases")
    draft = WorkflowBuild(workflow=read(workflow), rule_nodes=read(parent / "rule-nodes.json"))
    compiled, changes = compile_workflow(draft)
    policy = Policy.model_validate(read(parent / "policy.json"))
    validation = validate_workflow(compiled, policy, inputs.api)
    output.mkdir(parents=True)
    shutil.copyfile(Path(__file__), output / "driver-source.txt")
    for name in ("policy.json", "rule-nodes.json", "inputs.json"):
        shutil.copyfile(parent / name, output / name)
    shutil.copyfile(cases, output / "cases.json")
    write_json(output / "workflow.json", compiled.workflow)
    write_json(output / "validation.json", validation)
    write_json(output / "compiler-changes.json", changes)
    write_json(
        output / "parent.json",
        {
            "started_at": utc_now(),
            "source_fingerprint": source_fingerprint(),
            "parent": str(parent),
            "parent_workflow_sha256": digest(parent / "workflow.json"),
            "parent_trace_sha256": digest(parent / "trace.jsonl"),
            "corrected_reference": str(workflow),
            "reference_sha256": digest(workflow),
            "workflow_sha256": digest(output / "workflow.json"),
            "cases_sha256": digest(cases),
            "brief": brief,
            "holdout_opened": False,
            "generation_reused": True,
            "practice_seconds": seconds,
            "driver_source_sha256": digest(Path(__file__)),
            "read_retries": read_retries,
            "max_concurrent_calls": concurrency,
            "purpose": "Evaluate a reviewed public-development reference correction",
            "limits": (
                "One stochastic conversation repetition; "
                "not a fresh timed builder generation or submission candidate"
            ),
        },
    )
    if read_retries:
        from training_first_gepa import ReadResilientPractice

        practice = ReadResilientPractice(output, seconds)
    else:
        practice = Practice(output, seconds)
    # The kit passes three positional arguments here. Keep the official model,
    # case content and duration fixed while measuring queue/latency sensitivity.
    practice.freya.run_simulation = partial(practice.simulate, concurrency=concurrency)
    try:
        report = practice.execute(output / "cases.json", brief, f"reference-patch-{brief}")
    finally:
        practice.client.close()
    summary = {
        "ended_at": utc_now(),
        "passed": report["passed"],
        "total": report["total"],
        "failed": [row["id"] for row in report["rows"] if not row["pass"]],
        "runtime_health": read(output / "practice/runtime-health.json"),
        "promoted": False,
    }
    write_json(output / "report.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent", type=Path)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("cases", type=Path)
    parser.add_argument("brief", choices=("A", "B"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=float, default=900)
    parser.add_argument("--concurrency", type=int, choices=range(1, 6), default=5)
    parser.add_argument(
        "--read-retries",
        action="store_true",
        help="Bounded transient GET retries; never retry POST",
    )
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    with workspace_lock():
        result = capture_sync(
            args.output.resolve(),
            "reference_patch",
            lambda: run(
                args.parent.resolve(),
                args.workflow.resolve(),
                args.cases.resolve(),
                args.brief,
                args.output.resolve(),
                args.seconds,
                read_retries=args.read_retries,
                concurrency=args.concurrency,
            ),
            inputs={
                "parent": str(args.parent),
                "workflow": str(args.workflow),
                "generation_reused": True,
                "practice_seconds": args.seconds,
                "max_concurrent_calls": args.concurrency,
            },
        )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "passed": result["passed"],
                "total": result["total"],
                "clean": result["runtime_health"].get("usable_as_clean_model_comparison") is True,
            }
        )
    )
