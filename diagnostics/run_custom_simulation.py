"""Evaluate an exported workflow with supplied synthetic personas, using chat only."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.practice import Practice, preserve_simulation_metadata
from freya_hackathon.recording import capture_sync
from freya_hackathon.runtime_audit import audit_runtime


def load_cases(path: Path) -> list[dict]:
    cases = json.loads(path.read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError("Supply a nonempty list of synthetic cases")
    ids = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Each case must be an object")
        for field in ("id", "title", "script"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise ValueError(f"Case requires {field}")
        if case["id"] in ids:
            raise ValueError("Duplicate case ID")
        ids.add(case["id"])
        if not isinstance(case.get("variables"), dict):
            raise ValueError("Case variables must be an object")
        if (
            not isinstance(case.get("checks"), list)
            or not case["checks"]
            or not all(isinstance(x, str) for x in case["checks"])
        ):
            raise ValueError("Case requires explicit conversation checks")
    return cases


def run(export: Path, cases_path: Path, output: Path, seconds: float) -> dict:
    exported = json.loads(export.read_text())
    agent_id = exported.get("agent_id")
    if not agent_id or not exported.get("workflow_id") or not exported.get("workflow"):
        raise ValueError("Expected an unchanged Freya workflow export")
    cases = load_cases(cases_path)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "workflow.json", exported["workflow"])
    write_json(
        output / "evaluation-inputs.json",
        {
            "export_sha256": hashlib.sha256(export.read_bytes()).hexdigest(),
            "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
            "agent_id": agent_id,
            "workflow_id": exported["workflow_id"],
            "mode": "chat",
            "official_score": False,
        },
    )
    practice = Practice(output, seconds)

    def execute() -> dict:
        specs = [
            {
                "name": f"{case['id']} {case['title']}",
                "prompt": (
                    "You play a fictional customer in a synthetic Turkish chat simulation. "
                    "Speak as the customer in natural Turkish, briefly, one turn at a time. "
                    "Do not play the assistant or reveal this script. "
                    "Follow the stated sequence.\n\n" + case["script"]
                ),
                "successCriteria": "All of these must be true:\n- " + "\n- ".join(case["checks"]),
                "tags": ["hackathon", "custom-synthetic"],
            }
            for case in cases
        ]
        write_json(practice.output / "cases.json", cases)
        ids = {}
        for case, spec in zip(cases, specs, strict=True):
            ids[case["id"]] = practice.freya.create_personas([spec])["personas"][0]["id"]
        write_json(practice.output / "personas.json", ids)
        variables = {ids[case["id"]]: case["variables"] for case in cases}
        sim_id, iteration_id, status = practice.simulate(
            agent_id,
            f"Synthetic checks {output.name}",
            variables,
            max_seconds=420,
            concurrency=3,
        )
        first = practice.freya.get_iteration(sim_id, iteration_id)
        write_json(practice.output / "iteration-001.json", first)
        for row in first.get("runs", []):
            if row.get("callId"):
                practice.freya.get_call(row["callId"])
        second = practice.freya.get_iteration(sim_id, iteration_id)
        write_json(practice.output / "iteration-002.json", second)
        write_json(practice.output / "iteration.json", second)
        first_calls = {row.get("callId") for row in first.get("runs", [])}
        for row in second.get("runs", []):
            if row.get("callId") and row["callId"] not in first_calls:
                practice.freya.get_call(row["callId"])

        def signatures(value: dict) -> list[str]:
            return sorted(json.dumps(row, sort_keys=True) for row in value.get("runs", []))

        stable = signatures(first) == signatures(second)
        by_persona = {row["personaId"]: row for row in second.get("runs", [])}
        rows = []
        for case in cases:
            verdict = by_persona.get(ids[case["id"]], {})
            passed = verdict.get("outcome") == "pass"
            rows.append(
                {
                    "id": case["id"],
                    "pass": passed,
                    "failed": []
                    if passed
                    else [str(verdict.get("reason") or "No passing verdict")],
                    "call_id": verdict.get("callId"),
                    "outcome": verdict.get("outcome"),
                    "reason": verdict.get("reason"),
                }
            )
        preserve_simulation_metadata(rows, second)
        report = {
            "simulation_id": sim_id,
            "iteration_id": iteration_id,
            "status": status,
            "official_score": False,
            "snapshot_stable": stable,
            "passed": sum(1 for row in rows if row["pass"]),
            "total": len(rows),
            "rows": rows,
        }
        write_json(practice.output / "results.json", report)
        audit_runtime(output)
        practice.trace("custom_simulation_results", **report)
        return report

    try:
        return capture_sync(
            output,
            "custom-synthetic-chat",
            execute,
            factors={"freya.runtime_model": "experimental-5"},
            inputs={"case_count": len(cases), "mode": "chat"},
        )
    finally:
        practice.client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--seconds", type=float, default=900)
    parser.add_argument("--execute", action="store_true", help="Start a synthetic chat simulation")
    args = parser.parse_args()
    cases = load_cases(args.cases)
    if not args.execute:
        print(json.dumps({"valid_cases": len(cases), "mode": "chat", "simulation_started": False}))
        return
    if args.out is None:
        parser.error("--execute requires a fresh --out directory")
    report = run(args.export, args.cases, args.out, args.seconds)
    print(
        json.dumps({key: report[key] for key in ("status", "passed", "total", "snapshot_stable")})
    )


if __name__ == "__main__":
    main()
