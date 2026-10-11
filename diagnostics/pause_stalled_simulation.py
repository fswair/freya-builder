"""Pause only a synthetic chat simulation whose pending calls never initialized."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from freya_hackathon.builder import utc_now, write_json
from freya_hackathon.practice import Practice
from freya_hackathon.recording import capture_sync


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    simulation = json.loads((args.run / "practice/simulation.json").read_text())["simulation_id"]
    args.out.mkdir(parents=True, exist_ok=False)

    def pause():
        practice = Practice(args.out, 120)
        try:
            response = practice.api("GET", f"/api/v2/simulations/{simulation}")
            state = response.get("simulation") or response
            write_json(args.out / "before.json", state)
            if state.get("mode") != "chat" or state.get("status") != "running":
                raise ValueError("Only a currently running synthetic chat simulation can be paused")
            value = practice.api("GET", f"/api/v2/simulations/{simulation}/iterations")
            iterations = value.get("iterations") or value.get("items") or value
            iteration = practice.freya.get_iteration(simulation, iterations[0]["id"])
            write_json(practice.output / "iteration.json", iteration)
            pending = [r for r in iteration["runs"] if r.get("outcome") == "running"]
            if not pending:
                raise ValueError("No pending calls; leave simulation unchanged")
            created = datetime.fromisoformat(state["createdAt"].replace("Z", "+00:00"))
            age = (datetime.now(UTC) - created).total_seconds()
            if age < 900:
                raise ValueError("Simulation has not exceeded the 15-minute wait budget")
            for run in pending:
                call = practice.freya.get_call(run["callId"])
                if call.get("status") not in {"initiating", "180"} or call.get("messages"):
                    raise ValueError("Pending call may be active; leave simulation unchanged")
            practice.api("PUT", f"/api/v2/simulations/{simulation}", {"status": "paused"})
            response = practice.api("GET", f"/api/v2/simulations/{simulation}")
            after = response.get("simulation") or response
            write_json(args.out / "after.json", after)
            if after.get("status") != "paused":
                raise RuntimeError("Pause was not confirmed")
            result = {
                "time": utc_now(),
                "simulation_id": simulation,
                "reason": "Simulation exceeded 15 minutes; pending calls still have no messages",
                "pending_cases": [r["personaName"].split()[0] for r in pending],
                "server_attempts": {r["personaName"].split()[0]: r["attempts"] for r in pending},
                "status": "paused",
                "quality_score": None,
            }
            write_json(args.run / "interrupted.json", result)
            return result
        finally:
            practice.client.close()

    print(json.dumps(capture_sync(args.out, "pause-stalled-chat", pause)))


if __name__ == "__main__":
    main()
