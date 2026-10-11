"""Measure one fresh A build on fixed public and development training cases."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import ROOT, Builder, Reasoning, utc_now, write_json
from freya_hackathon.optimization import source_fingerprint, workspace_lock
from freya_hackathon.optimization_data import StudyConfig, load_split
from freya_hackathon.pipeline import run_pipeline


def check_baseline(baseline: Path, experiment: dict, cases: list[dict]) -> None:
    """Reject a confounded effort comparison before creating output or making requests."""
    previous = json.loads((baseline / "experiment.json").read_text())
    frozen = (
        "source_fingerprint",
        "guidance_sha256",
        "model",
        "public_ids",
        "training_ids",
        "generation_seconds",
        "total_seconds",
        "references",
        "holdout_opened",
    )
    changed = [key for key in frozen if previous.get(key) != experiment.get(key)]
    if json.loads((baseline / "cases.json").read_text()) != cases:
        changed.append("case contents")
    if changed:
        raise ValueError(f"Frozen comparison inputs changed: {', '.join(changed)}")
    health = json.loads((baseline / "A/practice/runtime-health.json").read_text())
    if health.get("usable_as_clean_model_comparison") is not True:
        raise ValueError("Baseline does not have clean evaluation evidence")


def run(
    output: Path,
    reasoning: Reasoning | None = None,
    baseline: Path | None = None,
    config_path: Path = ROOT / "configs/optimization/example.json",
) -> None:
    config = StudyConfig.read(config_path)
    inputs = config.inputs()["A"]
    public = [c for c in json.loads(config.public_cases.read_text()) if c["brief"] == "A"]
    train_ids = {"DEV-TR-A-001", "DEV-TR-A-015"}
    train = [c for c in load_split(config, "train") if c["id"] in train_ids]
    if len(public) != 10 or len(train) != 2:
        raise ValueError("Expected ten public cases and the two predetermined training probes")
    guidance = config.guidance.read_text()
    effort = reasoning or config.reasoning
    experiment = {
        "started_at": utc_now(),
        "config": str(config_path),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "source_fingerprint": source_fingerprint(),
        "guidance_sha256": hashlib.sha256(guidance.encode()).hexdigest(),
        "model": config.model,
        "reasoning": effort,
        "public_ids": [c["id"] for c in public],
        "training_ids": sorted(train_ids),
        "generation_seconds": 1200,
        "total_seconds": 1800,
        "call_seconds": 600,
        "attempts": 2,
        "references": inputs.manifest,
        "holdout_opened": False,
        "limits": "One development repetition; public references; not hidden-brief evidence.",
    }
    if baseline is not None:
        check_baseline(baseline, experiment, public + train)
        experiment["baseline"] = str(baseline)
    output.mkdir(parents=True, exist_ok=False)
    cases_path = output / "cases.json"
    write_json(cases_path, public + train)
    write_json(output / "experiment.json", experiment)
    builder = Builder(
        inputs,
        output / "A",
        model=config.model,
        reasoning=effort,
        seconds=1200,
        call_seconds=600,
        attempts=2,
        guidance=guidance,
    )
    result = run_pipeline(builder, cases_path, "A", "transfer-A", 1800)
    report = result["practice"]
    if report["status"] != "completed" or {r["id"] for r in report["rows"]} != {
        c["id"] for c in public + train
    }:
        raise ValueError("Incomplete evaluation evidence")
    groups = {}
    for name, cases in (("public", public), ("training", train)):
        ids = {c["id"] for c in cases}
        rows = [r for r in report["rows"] if r["id"] in ids]
        groups[name] = {
            "passed": sum(r["pass"] is True for r in rows),
            "total": len(rows),
            "failed": [r["id"] for r in rows if not r["pass"]],
        }
    health = json.loads((output / "A/practice/runtime-health.json").read_text())
    summary = {
        "ended_at": utc_now(),
        "groups": groups,
        "infrastructure_affected_cases": health["infrastructure_affected_cases"],
        "usable_as_clean_model_comparison": health["usable_as_clean_model_comparison"],
        "pipeline": json.loads((output / "A/pipeline.json").read_text()),
    }
    write_json(output / "report.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--reasoning", choices=("high", "xhigh"))
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/optimization/example.json")
    args = parser.parse_args()
    with workspace_lock():
        run(
            args.output.resolve(),
            args.reasoning,
            args.baseline.resolve() if args.baseline else None,
            args.config.resolve(),
        )
