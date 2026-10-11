"""Measure frozen candidate repeatability, then fresh end-to-end builder performance."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from time import monotonic

import httpx

from freya_hackathon.builder import KIT, ROOT, Builder, load_inputs, utc_now, write_json
from freya_hackathon.optimization import repeated_score, source_fingerprint, workspace_lock
from freya_hackathon.practice import Practice


def read(path: Path):
    return json.loads(path.read_text())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_report(run: Path) -> dict:
    report = read(run / "practice/results.json")
    health = read(run / "practice/runtime-health.json")
    if health.get("infrastructure_affected_cases") != []:
        raise RuntimeError(f"Infrastructure evidence is missing or affected: {run}")
    return report


def measure(output: Path, *, resume: bool = False) -> None:
    selected = read(ROOT / "diagnostics/current-candidates.json")
    output.mkdir(parents=True, exist_ok=resume)
    state = {
        "started_at": utc_now(),
        "status": "running",
        "scope": "Official public text practice; not hidden-case performance",
        "source_fingerprint": source_fingerprint(),
        "repeatability": {},
        "interrupted_runs": [],
        "fresh": {},
    }
    if resume:
        previous = read(output / "measurement.json")
        if previous["source_fingerprint"] != state["source_fingerprint"]:
            raise ValueError("Measurement code changed; use a new output directory")
        state["started_at"] = previous["started_at"]
        state["resumed_at"] = utc_now()

    def save() -> None:
        write_json(output / "measurement.json", state)

    def event(name: str, **fields) -> None:
        entry = {"time": utc_now(), "event": name, **fields}
        with (output / "progress.jsonl").open("a") as stream:
            stream.write(json.dumps(entry) + "\n")
        print(json.dumps(entry), flush=True)

    sources = {}
    reports = {}
    cases = {}
    save()
    try:
        for brief, candidate in selected["candidates"].items():
            source = ROOT / candidate["directory"]
            if sha(source / "workflow.json") != candidate["workflow_sha256"]:
                raise ValueError(f"Selected {brief} workflow changed")
            sources[brief] = source
            reports[brief] = [checked_report(source)]
            cases[brief] = source / "practice/cases.json"
            state["repeatability"][brief] = {
                "workflow_sha256": sha(source / "workflow.json"),
                "cases_sha256": sha(cases[brief]),
                "runs": [str(source)],
                "score": repeated_score(reports[brief], [c["id"] for c in read(cases[brief])]),
            }
        save()
        for repeat in (2, 3):
            for brief, source in sources.items():
                destination = output / f"repeat-{repeat}-{brief}"
                if (destination / "interrupted.json").exists():
                    state["interrupted_runs"].append(
                        {"run": str(destination), **read(destination / "interrupted.json")}
                    )
                    destination = output / f"repeat-{repeat}-{brief}-retry-1"
                    if (destination / "interrupted.json").exists():
                        raise RuntimeError("Repeated initialization failure; stop retries")
                existing = destination.exists()
                if existing:
                    if not resume or sha(destination / "workflow.json") != sha(
                        source / "workflow.json"
                    ):
                        raise ValueError("Existing repeat does not match frozen workflow")
                else:
                    destination.mkdir()
                    for name in ("workflow.json", "policy.json", "rule-nodes.json", "inputs.json"):
                        shutil.copyfile(source / name, destination / name)
                    write_json(
                        destination / "parent.json",
                        {
                            "source": str(source),
                            "workflow_sha256": sha(source / "workflow.json"),
                            "kind": "frozen-workflow-repeat",
                        },
                    )
                event("repeat_start", brief=brief, repeat=repeat)
                started = monotonic()
                if not (destination / "practice/results.json").exists():
                    practice = Practice(destination, 900, resume=existing)
                    try:
                        try:
                            if existing:
                                practice.collect()
                            else:
                                practice.execute(cases[brief], brief, f"measurement-repeat-{brief}")
                        except httpx.TransportError as exc:
                            if not (destination / "practice/simulation.json").exists():
                                raise
                            event("collection_retry", brief=brief, error_type=type(exc).__name__)
                            practice.collect()  # Read the same simulation; never launch another.
                    finally:
                        practice.client.close()
                report = checked_report(destination)
                reports[brief].append(report)
                result = state["repeatability"][brief]
                result["runs"].append(str(destination))
                result["score"] = repeated_score(
                    reports[brief], [c["id"] for c in read(cases[brief])]
                )
                save()
                event(
                    "repeat_complete",
                    brief=brief,
                    repeat=repeat,
                    passed=report["passed"],
                    total=report["total"],
                    seconds=monotonic() - started,
                )
        for brief in sources:
            documents = [KIT / "briefs/brief-a.md"]
            if brief == "B":
                documents.append(KIT / "briefs/brief-b.md")
            destination = output / f"fresh-{brief}"
            event("fresh_start", brief=brief, model="gpt-6-astra", reasoning="high")
            started = monotonic()
            try:
                if not destination.exists():
                    builder = Builder(
                        load_inputs(documents, KIT / "tools/openapi.json", KIT),
                        destination,
                        model="gpt-6-astra",
                        reasoning="high",
                        seconds=1200,
                    )
                    asyncio.run(builder.run())
                trace = [
                    json.loads(line)
                    for line in (destination / "trace.jsonl").read_text().splitlines()
                ]
                generation = next(row for row in reversed(trace) if row["event"] == "builder_end")
                if generation["status"] != "locally_validated":
                    raise RuntimeError("Fresh generation did not complete successfully")
                if not (destination / "practice/results.json").exists():
                    practice = Practice(
                        destination, 900, resume=(destination / "practice").exists()
                    )
                    try:
                        try:
                            if (destination / "practice/simulation.json").exists():
                                practice.collect()
                            else:
                                practice.execute(cases[brief], brief, f"measurement-fresh-{brief}")
                        except httpx.TransportError:
                            if not (destination / "practice/simulation.json").exists():
                                raise
                            practice.collect()
                    finally:
                        practice.client.close()
                report = checked_report(destination)
                trace = [
                    json.loads(line)
                    for line in (destination / "trace.jsonl").read_text().splitlines()
                ]
                practice_start = next(
                    row["time"] for row in trace if row["event"] == "practice_start"
                )
                practice_end = next(
                    row["time"]
                    for row in reversed(trace)
                    if row["event"].startswith("practice_results")
                )
                state["fresh"][brief] = {
                    "status": "evaluated",
                    "passed": report["passed"],
                    "total": report["total"],
                    "failed": [row["id"] for row in report["rows"] if not row["pass"]],
                    "generation_seconds": generation["elapsed_seconds"],
                    "practice_seconds": (
                        datetime.fromisoformat(practice_end)
                        - datetime.fromisoformat(practice_start)
                    ).total_seconds(),
                    "contiguous_pipeline": False,
                    "timing_note": "Separate generation/practice timing; queue wait excluded",
                    "run": str(destination),
                    "workflow_sha256": sha(destination / "workflow.json"),
                    "reuse": False,
                }
            except Exception as exc:
                state["fresh"][brief] = {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "elapsed_seconds": monotonic() - started,
                    "run": str(destination),
                    "reuse": False,
                }
                # Do not launch another simulation while a timed-out one is still active.
                pending = destination / "practice/simulation-state.json"
                if pending.exists() and read(pending).get("status") not in {
                    "completed",
                    "failed",
                    "paused",
                }:
                    save()
                    raise
            save()
            event("fresh_complete", brief=brief, **state["fresh"][brief])
        state["status"] = "completed"
    except Exception as exc:
        state["status"] = "aborted"
        state["error_type"] = type(exc).__name__
        raise
    finally:
        state["ended_at"] = utc_now()
        save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    with workspace_lock():
        measure(args.out.resolve(), resume=args.resume)


if __name__ == "__main__":
    main()
