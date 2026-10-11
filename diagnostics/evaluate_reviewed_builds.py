"""Evaluate completed fresh builds sequentially and retain a qualified comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import monotonic, sleep

import httpx

from freya_hackathon.builder import ROOT, utc_now, write_json
from freya_hackathon.optimization import source_fingerprint, workspace_lock
from freya_hackathon.practice import Practice


def read(path: Path):
    return json.loads(path.read_text())


def identity(rows: list[dict]) -> list[tuple[str, str]]:
    return [(row["source"], row["sha256"]) for row in rows]


def evaluate(output: Path) -> None:
    experiment = read(output / "experiment.json")
    summary_path = output / "comparison.json"
    summary: dict = (
        read(summary_path)
        if summary_path.exists()
        else {
            "started_at": utc_now(),
            "source_fingerprint": source_fingerprint(),
            "scope": experiment["scope"],
            "comparison_limits": experiment["comparison_limits"],
            "results": {},
        }
    )
    if summary["source_fingerprint"] != source_fingerprint():
        raise ValueError("Builder source changed during the experiment")
    write_json(summary_path, summary)
    deadline = monotonic() + 1500
    pending = [b for b in ("A", "B") if b not in summary["results"]]
    while pending:
        ready = None
        for brief in pending:
            run = output / brief
            trace = run / "trace.jsonl"
            if not trace.exists():
                continue
            # Ignore an incomplete final line while the producer is writing it.
            events = [
                json.loads(line)
                for line in trace.read_text().splitlines(keepends=True)
                if line.endswith("\n")
            ]
            ends = [e for e in events if e["event"] == "builder_end"]
            if ends:
                ready = (brief, run, ends[-1])
                break
        if ready is None:
            if monotonic() > deadline:
                raise TimeoutError("Fresh builders did not finish before the observation deadline")
            sleep(2)
            continue
        brief, run, generation = ready
        result = {"generation": generation, "run": str(run)}
        if generation["status"] != "locally_validated":
            result["status"] = "generation_failed"
        else:
            baseline = ROOT / experiment["baseline"][brief]
            baseline_report = read(baseline / "practice/results.json")
            candidate_inputs = read(run / "inputs.json")
            if experiment.get("allow_development_references"):
                result["development_references"] = [
                    row
                    for row in candidate_inputs
                    if row["source"].startswith("development-reference-")
                ]
                candidate_inputs = [
                    row
                    for row in candidate_inputs
                    if not row["source"].startswith("development-reference-")
                ]
            if identity(candidate_inputs) != identity(read(baseline / "inputs.json")):
                raise ValueError("Candidate and baseline inputs differ")
            if not (run / "practice/results.json").exists():
                existing = (run / "practice/simulation.json").exists()
                practice = Practice(run, 900, resume=existing)
                try:
                    try:
                        if existing:
                            practice.collect()
                        else:
                            practice.execute(
                                baseline / "practice/cases.json", brief, f"reviewed-builder-{brief}"
                            )
                    except (httpx.TransportError, TimeoutError) as exc:
                        if not (run / "practice/simulation.json").exists():
                            raise
                        result["collection_recovery"] = type(exc).__name__
                        # Recover the same simulation. Never start the next one if still active.
                        practice.collect()
                finally:
                    practice.client.close()
            report = read(run / "practice/results.json")
            before = {r["id"]: r["pass"] for r in baseline_report["rows"]}
            after = {r["id"]: r["pass"] for r in report["rows"]}
            if report["status"] != "completed" or set(before) != set(after):
                raise ValueError("Incomplete or noncomparable practice results")
            health = read(run / "practice/runtime-health.json")
            result.update(
                status="evaluated",
                baseline_passed=baseline_report["passed"],
                passed=report["passed"],
                total=report["total"],
                fixed=[k for k in before if not before[k] and after[k]],
                regressed=[k for k in before if before[k] and not after[k]],
                failed=[k for k in after if not after[k]],
                infrastructure_affected_cases=health["infrastructure_affected_cases"],
                review_enabled=(run / "review.json").exists(),
                review_findings=(
                    read(run / "review.json")["findings"] if (run / "review.json").exists() else []
                ),
            )
        summary["results"][brief] = result
        write_json(summary_path, summary)
        print(
            json.dumps(
                {
                    "brief": brief,
                    "status": result["status"],
                    "passed": result.get("passed"),
                    "total": result.get("total"),
                }
            ),
            flush=True,
        )
        pending.remove(brief)
    summary["ended_at"] = utc_now()
    write_json(summary_path, summary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with workspace_lock():
        evaluate(args.output.resolve())


if __name__ == "__main__":
    main()
