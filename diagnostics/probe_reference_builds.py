"""Measure four predetermined training cases against frozen reference-based builds."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import httpx

from freya_hackathon.builder import ROOT, utc_now, write_json
from freya_hackathon.optimization import source_fingerprint, workspace_lock
from freya_hackathon.optimization_data import StudyConfig, load_split
from freya_hackathon.practice import Practice

PROBES = {
    "A": ["DEV-TR-A-001", "DEV-TR-A-015"],
    "B": ["DEV-TR-B-003", "DEV-TR-B-007"],
}


def read(path: Path):
    return json.loads(path.read_text())


def evaluate(source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    config = StudyConfig.read(ROOT / "configs/optimization/example.json")
    rows = load_split(config, "train")
    by_id = {row["id"]: row for row in rows}
    summary_path = output / "report.json"
    summary: dict = (
        read(summary_path)
        if summary_path.exists()
        else {
            "started_at": utc_now(),
            "source_fingerprint": source_fingerprint(),
            "build_root": str(source),
            "scope": "Four predetermined development training cases; one repetition each.",
            "limits": "Not holdout, not an optimization run, not a competition score.",
            "selected_ids": PROBES,
            "results": {},
        }
    )
    if summary["source_fingerprint"] != source_fingerprint():
        raise ValueError("Source changed during probe")
    if summary["build_root"] != str(source) or summary["selected_ids"] != PROBES:
        raise ValueError("Probe inputs changed")
    write_json(summary_path, summary)
    for brief, ids in PROBES.items():
        if brief in summary["results"]:
            continue
        run = output / brief
        run.mkdir(parents=True, exist_ok=True)
        parent = source / brief
        for name in ("workflow.json", "policy.json", "rule-nodes.json", "inputs.json"):
            destination = run / name
            if destination.exists():
                if destination.read_bytes() != (parent / name).read_bytes():
                    raise ValueError(f"Frozen parent changed: {name}")
            else:
                shutil.copyfile(parent / name, destination)
        workflow_sha = hashlib.sha256((run / "workflow.json").read_bytes()).hexdigest()
        write_json(run / "parent.json", {"build": str(parent), "workflow_sha256": workflow_sha})
        cases = [by_id[case_id] for case_id in ids]
        cases_path = run / "cases.json"
        if cases_path.exists() and read(cases_path) != cases:
            raise ValueError("Training cases changed")
        write_json(cases_path, cases)
        if not (run / "practice/results.json").exists():
            existing = (run / "practice/simulation.json").exists()
            practice = Practice(run, 900, resume=existing)
            try:
                try:
                    if existing:
                        practice.collect()
                    else:
                        practice.execute(cases_path, brief, f"reference-probe-{brief}")
                except (httpx.TransportError, TimeoutError):
                    if not (run / "practice/simulation.json").exists():
                        raise
                    practice.collect()
            finally:
                practice.client.close()
        report = read(run / "practice/results.json")
        if report["status"] != "completed" or {r["id"] for r in report["rows"]} != set(ids):
            raise ValueError("Incomplete probe evidence")
        health = read(run / "practice/runtime-health.json")
        summary["results"][brief] = {
            "run": str(run),
            "workflow_sha256": workflow_sha,
            "passed": report["passed"],
            "total": report["total"],
            "failed": [row["id"] for row in report["rows"] if not row["pass"]],
            "infrastructure_affected_cases": health["infrastructure_affected_cases"],
        }
        write_json(summary_path, summary)
        print(json.dumps({"brief": brief, **summary["results"][brief]}), flush=True)
    summary["ended_at"] = utc_now()
    write_json(summary_path, summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with workspace_lock():
        evaluate(args.source.resolve(), args.output.resolve())
