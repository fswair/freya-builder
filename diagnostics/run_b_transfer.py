"""Measure one fresh B build on fixed public and targeted development cases."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import ROOT, Builder, utc_now, write_json
from freya_hackathon.optimization import source_fingerprint, workspace_lock
from freya_hackathon.optimization_data import StudyConfig, load_split
from freya_hackathon.pipeline import run_pipeline


def run(output: Path, config_path: Path = ROOT / "configs/optimization/example.json") -> None:
    config = StudyConfig.read(config_path)
    inputs = config.inputs()["B"]
    public = [c for c in json.loads(config.public_cases.read_text()) if c["brief"] == "B"]
    train_ids = {"DEV-TR-B-001", "DEV-TR-B-005", "DEV-TR-B-007", "DEV-TR-B-015"}
    train = [c for c in load_split(config, "train") if c["id"] in train_ids]
    if len(public) != 3 or {c["id"] for c in train} != train_ids:
        raise ValueError("Expected three public cases and four predetermined training probes")
    output.mkdir(parents=True, exist_ok=False)
    cases_path = output / "cases.json"
    write_json(cases_path, public + train)
    guidance = config.guidance.read_text()
    write_json(
        output / "experiment.json",
        {
            "started_at": utc_now(),
            "config": str(config_path),
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "source_fingerprint": source_fingerprint(),
            "guidance_sha256": hashlib.sha256(guidance.encode()).hexdigest(),
            "model": config.model,
            "reasoning": config.reasoning,
            "public_ids": [c["id"] for c in public],
            "training_ids": sorted(train_ids),
            "generation_seconds": 1200,
            "total_seconds": 1800,
            "references": inputs.manifest,
            "holdout_opened": False,
            "limits": "One development repetition; public references; not hidden-brief evidence.",
            "purpose": "Consent and product-scope transfer, plus the prior sandbox-error case.",
        },
    )
    builder = Builder(
        inputs,
        output / "B",
        model=config.model,
        reasoning=config.reasoning,
        seconds=1200,
        call_seconds=600,
        attempts=2,
        guidance=guidance,
    )
    result = run_pipeline(builder, cases_path, "B", "transfer-B", 1800)
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
    health = json.loads((output / "B/practice/runtime-health.json").read_text())
    summary = {
        "ended_at": utc_now(),
        "groups": groups,
        "runtime_health": health,
        "pipeline": json.loads((output / "B/pipeline.json").read_text()),
    }
    write_json(output / "report.json", summary)
    print(
        json.dumps(
            {
                "output": str(output),
                "groups": groups,
                "clean": health.get("usable_as_clean_model_comparison") is True,
                "health_path": str(output / "B/practice/runtime-health.json"),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/optimization/example.json")
    args = parser.parse_args()
    with workspace_lock():
        run(args.output.resolve(), args.config.resolve())
