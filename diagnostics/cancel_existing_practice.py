"""Stop one existing chat simulation without deleting its evidence or retrying POST."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from training_first_gepa import ReadResilientPractice

from freya_hackathon.builder import utc_now, write_json
from freya_hackathon.recording import capture_sync


def cancel(source: Path, out: Path) -> dict:
    saved = json.loads((source / "practice/simulation.json").read_text())
    simulation_id = saved["simulation_id"]
    practice = ReadResilientPractice(out, 90)
    report = {"started_at": utc_now(), "simulation_id": simulation_id, "post_attempted": False}
    try:
        response = practice.api("GET", f"/api/v2/simulations/{simulation_id}")
        state = response.get("simulation") or response
        write_json(out / "before.json", state)
        if state.get("id") != simulation_id or state.get("mode") != "chat":
            raise ValueError("Refusing to cancel an unmatched or non-chat simulation")
        report["before_status"] = state.get("status")
        if state.get("status") == "running":
            report["post_attempted"] = True
            write_json(out / "action.json", report)
            result = practice.api("POST", f"/api/v2/simulations/{simulation_id}/cancel")
            write_json(out / "cancel-response.json", result)
        response = practice.api("GET", f"/api/v2/simulations/{simulation_id}")
        state = response.get("simulation") or response
        write_json(out / "after.json", state)
        report["after_status"] = state.get("status")
        report["post_retried"] = False
        return report
    finally:
        practice.client.close()
        report["ended_at"] = utc_now()
        write_json(out / "report.json", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = capture_sync(
        args.out,
        "existing_simulation_cancel",
        lambda: cancel(args.source, args.out),
        inputs={"source": str(args.source), "new_simulation": False, "delete": False},
    )
    print(json.dumps(result))
