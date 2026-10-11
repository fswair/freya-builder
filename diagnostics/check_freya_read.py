"""Make one bounded read of an existing simulation, without creating a new one."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

from freya_hackathon.builder import utc_now, write_json
from freya_hackathon.practice import Practice
from freya_hackathon.recording import capture_sync


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=8)
    args = parser.parse_args()
    if not 0 < args.seconds <= 15:
        parser.error("Read probe budget must be between 0 and 15 seconds")
    simulation = json.loads((args.source / "practice/simulation.json").read_text())
    simulation_id = simulation.get("id") or simulation.get("simulation_id")
    if not isinstance(simulation_id, str) or not simulation_id:
        raise ValueError("Missing existing simulation id")
    args.out.mkdir(parents=True, exist_ok=False)

    def probe() -> dict:
        practice = Practice(args.out, args.seconds)
        started = time.monotonic()
        report = {
            "started_at": utc_now(),
            "new_simulation": False,
            "requests": 1,
            "method": "GET",
        }
        try:
            response = practice.api("GET", f"/api/v2/simulations/{simulation_id}")
            report["reachable"] = True
            report["response_type"] = type(response).__name__
            if isinstance(response, dict):
                state = response.get("simulation") or response
                if isinstance(state, dict):
                    report["simulation_status"] = state.get("status")
                    report["server_updated_at"] = state.get("updatedAt")
                    metrics = state.get("metrics")
                    if isinstance(metrics, dict):
                        report["metrics"] = {
                            key: metrics[key]
                            for key in ("totalRuns", "passedRuns", "failedRuns")
                            if key in metrics
                        }
        except Exception as exc:
            report["reachable"] = False
            report["error_type"] = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                report["http_status"] = exc.response.status_code
        finally:
            practice.client.close()
        report["seconds"] = round(time.monotonic() - started, 3)
        report["ended_at"] = utc_now()
        write_json(args.out / "report.json", report)
        return report

    result = capture_sync(args.out, "existing_simulation_read_probe", probe)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
