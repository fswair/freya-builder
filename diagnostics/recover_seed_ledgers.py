"""Read missing ledgers from recorded simulation seeds without changing official scores."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from freya_hackathon.builder import utc_now, write_json
from freya_hackathon.practice import Practice, iteration_runs_by_case
from freya_hackathon.recording import capture_sync, redact, secrets_to_redact


def select_missing_ledgers(
    simulation_id: str, state: dict, iteration: dict, seeds: dict, result: dict
) -> list[dict]:
    """Require an unambiguous recorded persona-to-seed join in a terminal simulation."""
    if state.get("status") not in {"completed", "failed"}:
        raise ValueError("Simulation must be terminal before seed-ledger recovery")
    if (
        state.get("id") != simulation_id
        or iteration.get("iteration", {}).get("simulationId") != simulation_id
        or result.get("simulation_id") != simulation_id
        or result.get("iteration_id") != iteration.get("iteration", {}).get("id")
    ):
        raise ValueError("Simulation or iteration identity mismatch")
    grouped = iteration_runs_by_case(iteration)
    persona_counts = Counter(row.get("personaId") for row in iteration.get("runs", []))
    seed_ids = []
    for value in seeds.values():
        customer_id = value.get("customer_id") if isinstance(value, dict) else None
        if not isinstance(customer_id, str) or not re.fullmatch(
            r"c_[A-Za-z0-9_-]{1,158}", customer_id
        ):
            raise ValueError("Invalid recorded synthetic customer identifier")
        seed_ids.append(customer_id)
    if len(set(seed_ids)) != len(seed_ids):
        raise ValueError("Multiple personas share a recorded customer identifier")
    result_ids = [row["id"] for row in result["rows"]]
    if len(set(result_ids)) != len(result_ids):
        raise ValueError("Duplicate result case identifiers")
    selected = []
    for row in result["rows"]:
        matches = grouped.get(row["id"], [])
        if len(matches) != 1:
            raise ValueError("Missing or ambiguous iteration case join")
        judge = matches[0]
        persona_id = judge.get("personaId")
        if persona_counts[persona_id] != 1 or persona_id not in seeds:
            raise ValueError("Missing or ambiguous persona-to-seed join")
        customer_id = seeds[persona_id]["customer_id"]
        if (
            row.get("simulation_run_id") != judge.get("id")
            or row.get("call_id") != judge.get("callId")
            or row.get("simulation_outcome") != judge.get("outcome")
        ):
            raise ValueError("Recorded result does not match iteration evidence")
        if row.get("customer_id"):
            if row["customer_id"] != customer_id:
                raise ValueError("Call-derived customer identifier conflicts with recorded seed")
            continue
        selected.append(
            {
                "case_id": row["id"],
                "persona_id": persona_id,
                "customer_id": customer_id,
                "customer_id_source": "recorded_simulation_inputs",
                "call_id": row.get("call_id"),
                "official_outcome": judge.get("outcome"),
                "original_combined_pass": row["pass"],
                "original_failures": row["failed"],
            }
        )
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Original run containing recorded seeds")
    parser.add_argument("recovered", type=Path, help="Final recovered run; remains untouched")
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    source = args.source / "practice"
    recovered = args.recovered / "practice"
    files = {
        "simulation": source / "simulation.json",
        "simulation-inputs": source / "simulation-inputs.json",
        "simulation-state": recovered / "simulation-state.json",
        "iteration": recovered / "iteration.json",
        "results": recovered / "results.json",
        "cases": recovered / "cases.json",
    }
    data = {key: json.loads(path.read_text()) for key, path in files.items()}
    selected = select_missing_ledgers(
        data["simulation"]["simulation_id"],
        data["simulation-state"],
        data["iteration"],
        data["simulation-inputs"],
        data["results"],
    )
    cases = {case["id"]: case for case in data["cases"]}
    if len(cases) != len(data["cases"]) or any(row["case_id"] not in cases for row in selected):
        raise ValueError("Missing or duplicate scoring cases")
    args.out.mkdir(parents=True, exist_ok=False)
    provenance = {
        key: {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for key, path in files.items()
    }
    write_json(args.out / "source-provenance.json", provenance)

    def inspect() -> dict:
        practice = Practice(args.out, 150)
        try:
            for row in selected:
                try:
                    ledger = practice.bank("/v1/practice/ledger/" + row["customer_id"])
                    ledger = redact(ledger, secrets_to_redact())
                    write_json(practice.output / f"ledger-{row['customer_id']}.json", ledger)
                    score = practice.cases.score_ledger(cases[row["case_id"]], ledger)
                    row.update(
                        read_succeeded=True,
                        ledger_passed=not score["failed"],
                        ledger_score=score,
                        ledger_call_count=len(ledger.get("calls", [])),
                    )
                except Exception as exc:
                    row.update(read_succeeded=False, error_type=type(exc).__name__)
        finally:
            practice.client.close()
        report = {
            "captured_at": utc_now(),
            "simulation_id": data["simulation"]["simulation_id"],
            "new_call": False,
            "new_simulation": False,
            "bank_method": "GET",
            "official_outcome_overridden": False,
            "usable_as_clean_model_comparison": False,
            "scope": "Additional bank evidence only; original result files and failures unchanged.",
            "limit": (
                "Ledgers belong to seeded personas and may contain multiple simulation attempts. "
                "They do not repair missing call traces or prove the final attempt succeeded."
            ),
            "sources": provenance,
            "rows": selected,
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "seed_ledger_recovery_read", inspect)
    print(json.dumps({"output": str(args.out), "rows": len(report["rows"])}))


if __name__ == "__main__":
    main()
