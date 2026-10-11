"""Read a running synthetic simulation without modifying its run or starting calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from freya_hackathon.builder import utc_now, write_json
from freya_hackathon.practice import Practice
from freya_hackathon.recording import capture_sync


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--iteration-only", action="store_true")
    parser.add_argument("--extra-call-id", action="append", default=[])
    args = parser.parse_args()
    simulation = json.loads((args.run / "practice" / "simulation.json").read_text())[
        "simulation_id"
    ]
    args.out.mkdir(parents=True, exist_ok=False)

    def inspect() -> list[dict]:
        practice = Practice(args.out, seconds=120)
        try:
            value = practice.api("GET", f"/api/v2/simulations/{simulation}/iterations")
            iterations = value.get("iterations") or value.get("items") or value
            iteration = practice.freya.get_iteration(simulation, iterations[0]["id"])
            write_json(practice.output / "iteration.json", iteration)
            rows = []
            for run in iteration["runs"]:
                call = (
                    practice.freya.get_call(run["callId"])
                    if run.get("callId") and not args.iteration_only
                    else {}
                )
                call = call.get("call", call)
                rows.append(
                    {
                        "case": run["personaName"].split()[0],
                        "outcome": run.get("outcome"),
                        "reason": run.get("reason"),
                        "call_id": run.get("callId"),
                        "ended": call.get("endedReason"),
                        "messages": (
                            None if args.iteration_only else len(call.get("messages", []))
                        ),
                        "attempts": run.get("attempts"),
                        "started_at": run.get("startedAt"),
                    }
                )
            extra_calls = []
            for call_id in args.extra_call_id:
                call = practice.freya.get_call(call_id)
                call = call.get("call", call)
                extra_calls.append(
                    {
                        "call_id": call_id,
                        "ended": call.get("endedReason"),
                        "messages": len(call.get("messages", [])),
                    }
                )
            write_json(
                args.out / "inspection.json",
                {"time": utc_now(), "rows": rows, "extra_calls": extra_calls},
            )
            return rows
        finally:
            practice.client.close()

    result = capture_sync(
        args.out,
        "inspection",
        inspect,
        inputs={"simulation_id": simulation, "extra_call_ids": args.extra_call_id},
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
