"""Measure visible transcript reply gaps in completed synthetic simulation records."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.recording import capture_sync


def call_latency(path: Path) -> dict:
    call = json.loads(path.read_text())["call"]
    turns = []
    pending = []
    missing_timestamps = 0
    for message in call.get("messages", []):
        role = message.get("role")
        if role not in ("user", "assistant") or not message.get("message"):
            continue
        seconds = message.get("secondsFromStart")
        if not isinstance(seconds, (int, float)):
            missing_timestamps += 1
            continue
        if role == "user":
            pending.append(seconds)
        elif pending:
            turns.append(
                {
                    "first_user_seconds": pending[0],
                    "last_user_seconds": pending[-1],
                    "assistant_seconds": seconds,
                    "user_messages": len(pending),
                    "gap_from_first_seconds": seconds - pending[0],
                    "gap_from_last_seconds": seconds - pending[-1],
                }
            )
            pending = []
    gaps = [turn["gap_from_last_seconds"] for turn in turns]
    duration = call.get("duration")
    trailing_gap = (
        duration - pending[-1] if pending and isinstance(duration, (int, float)) else None
    )
    return {
        "call_id": call["id"],
        "source": str(path),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "duration_seconds": duration,
        "ended_reason": call.get("endedReason"),
        "responded_user_groups": len(turns),
        "trailing_user_messages": len(pending),
        "trailing_gap_seconds": trailing_gap,
        "median_reply_gap_seconds": statistics.median(gaps) if gaps else None,
        "max_reply_gap_seconds": max(gaps) if gaps else None,
        "replies_over_20_seconds": sum(gap > 20 for gap in gaps),
        "missing_timestamps": missing_timestamps,
        "nonmonotonic_reply_gaps": sum(gap < 0 for gap in gaps),
        "turns": turns,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def analyze():
        results = []
        for run in args.runs:
            iteration_path = run / "practice/iteration.json"
            iteration = json.loads(iteration_path.read_text())
            calls = []
            for row in iteration["runs"]:
                path = run / "practice" / f"call-{row['callId']}.json"
                if not path.exists():
                    calls.append({"call_id": row["callId"], "missing_call": True})
                    continue
                result = call_latency(path)
                result.update(
                    case_id=row["personaName"].split()[0], official_outcome=row["outcome"]
                )
                calls.append(result)
            results.append(
                {
                    "run": str(run),
                    "calls": calls,
                    "iteration_sha256": hashlib.sha256(iteration_path.read_bytes()).hexdigest(),
                    "workflow_sha256": hashlib.sha256(
                        (run / "workflow.json").read_bytes()
                    ).hexdigest(),
                }
            )
        report = {
            "results": results,
            "network_requests": False,
            "limits": "Visible transcript timing only, not model latency attribution. "
            "No adjusted score. A trailing customer message may be an ordinary "
            "goodbye; inspect context before calling it a timeout. "
            "Consecutive customer messages form one reply group.",
        }
        write_json(args.out / "report.json", report)
        return report

    capture_sync(args.out, "offline_transcript_reply_gaps", analyze)
    print(args.out / "report.json")


if __name__ == "__main__":
    main()
