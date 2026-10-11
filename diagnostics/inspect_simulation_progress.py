"""Read existing iteration progress; optional bounded GET retries never create a simulation."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from uuid import UUID

from training_first_gepa import ReadResilientPractice

from freya_hackathon.builder import utc_now, write_json
from freya_hackathon.practice import Practice
from freya_hackathon.recording import capture_sync, redact, secrets_to_redact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--read-retries", action="store_true")
    parser.add_argument("--iteration-id", type=UUID)
    args = parser.parse_args()
    if not 0 < args.seconds <= 180:
        parser.error("Progress read budget must be between 0 and 180 seconds")
    simulation = json.loads((args.source / "practice/simulation.json").read_text())
    simulation_id = simulation["simulation_id"]
    args.out.mkdir(parents=True, exist_ok=False)

    def inspect() -> dict:
        practice_type = ReadResilientPractice if args.read_retries else Practice
        practice = practice_type(args.out, args.seconds)
        report: dict = {
            "simulation_id": simulation_id,
            "new_simulation": False,
            "requests": 0,
            "request_count_scope": "logical GET operations; retry attempts are recorded in trace",
            "read_retries": args.read_retries,
            "budget_seconds": args.seconds,
            "iteration_id_source": "provided" if args.iteration_id else "iteration-list",
        }
        try:
            path = f"/api/v2/simulations/{simulation_id}/iterations"
            if args.iteration_id:
                iterations = [{"id": str(args.iteration_id)}]
            else:
                report["requests"] += 1
                iterations = practice.api("GET", path)
            if isinstance(iterations, dict):
                iterations = iterations.get("iterations") or iterations.get("items") or []
            if not iterations:
                report["status"] = "no_iterations"
            else:
                # Preserve the readable list result even if the detail GET times out.
                report["iteration_id"] = iterations[0]["id"]
                report["iteration_list_status"] = iterations[0].get("status")
                report["requests"] += 1
                iteration = practice.api("GET", path + "/" + report["iteration_id"], limit=100)
                write_json(args.out / "iteration.json", redact(iteration, secrets_to_redact()))
                rows = iteration.get("runs", [])
                metadata = iteration.get("iteration") or iteration
                report.update(
                    returned_iteration_id=metadata.get("id"),
                    returned_simulation_id=metadata.get("simulationId"),
                    identity_matches=(
                        metadata.get("id") == report["iteration_id"]
                        and metadata.get("simulationId") == simulation_id
                    ),
                    status=metadata.get("status"),
                    runs_returned=len(rows),
                    possibly_truncated=len(rows) == 100,
                    statuses=dict(Counter(str(row.get("status")) for row in rows)),
                    outcomes=dict(Counter(str(row.get("outcome")) for row in rows)),
                    runs=[
                        {
                            "case_id": str(row.get("personaName", "")).split(" ")[0],
                            "run_id": row.get("id"),
                            "persona_id": row.get("personaId"),
                            "status": row.get("status"),
                            "outcome": row.get("outcome"),
                            "call_id": row.get("callId"),
                            "attempts": row.get("attempts"),
                            "max_attempts": row.get("maxAttempts"),
                            "started_at": row.get("startedAt"),
                            "ended_at": row.get("endedAt"),
                            "error_detail": {
                                key: row[key]
                                for key in ("error", "reason", "errorMessage")
                                if key in row
                                and row.get("outcome")
                                in {"error", "fail", "inconclusive", "retrying"}
                            },
                        }
                        for row in rows
                    ],
                )
        except Exception as exc:
            report["read_error_type"] = type(exc).__name__
        finally:
            practice.client.close()
        report["observed_at"] = utc_now()
        safe_report = redact(report, secrets_to_redact())
        write_json(args.out / "report.json", safe_report)
        return safe_report

    result = capture_sync(args.out, "existing_simulation_progress_read", inspect)
    print(json.dumps({key: value for key, value in result.items() if key != "runs"}))


if __name__ == "__main__":
    main()
