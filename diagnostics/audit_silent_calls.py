"""Summarize observed post-customer silence without assigning fault or changing scores."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.recording import capture_sync


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def action():
        root = args.run / "practice"
        iteration = json.loads((root / "iteration.json").read_text())
        rows = []
        for item in iteration["runs"]:
            path = root / f"call-{item.get('callId')}.json"
            row = {"id": item["personaName"].split()[0], "call_id": item.get("callId"),
                   "official_outcome": item.get("outcome"), "raw": str(path)}
            if not path.exists():
                row["missing_call"] = True
                rows.append(row)
                continue
            row["raw_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            call = json.loads(path.read_text())["call"]
            messages = call.get("messages", [])
            dialogue = [m for m in messages if m.get("role") in ("user", "assistant")]
            events = []
            for message in messages:
                if message.get("role") != "system":
                    continue
                try:
                    event = json.loads(message.get("message", ""))
                except (ValueError, TypeError):
                    continue
                if isinstance(event, dict):
                    events.append(event)
            user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
            user_time = float(user.get("secondsFromStart", 0)) if user else None
            after = [m for m in messages if user_time is not None
                     and float(m.get("secondsFromStart", 0)) > user_time]
            duration = float(call.get("duration") or 0)
            decide = sum(e.get("event") == "node_enter" and e.get("node") == "decide"
                         for e in events)
            early_silence = (user is not None and len(dialogue) == 2 and not after
                             and duration - user_time >= 45 and decide == 0)
            no_greeting_silence = not dialogue and duration >= 45
            row.update(duration=duration, dialogue_messages=len(dialogue),
                       last_customer_seconds=user_time, events_after_last_customer=len(after),
                       decide_entries=decide, ended_reason=call.get("endedReason"),
                       extraction_errors=sum(e.get("event") == "extraction_error"
                                             for e in events),
                       early_silence=early_silence, no_greeting_silence=no_greeting_silence)
            rows.append(row)
        report = {"run": str(args.run), "cases": rows,
                  "early_silence_count": sum(r.get("early_silence", False) for r in rows),
                  "no_greeting_silence_count": sum(r.get("no_greeting_silence", False)
                                                   for r in rows),
                  "total_calls": len(rows), "official_score_adjusted": False,
                  "interpretation": "Early silence means one greeting and one customer reply, "
                  "then at least45s until the end with no further recorded event and no decide "
                  "entry. No-greeting silence means zero dialogue and at least45s duration. "
                  "These are observed boundaries, not proof of "
                  "an infrastructure, model, simulator or workflow cause."}
        write_json(args.out / "report.json", report)
        return report

    result = capture_sync(args.out, "post_customer_silence_audit", action)
    print(json.dumps({k: result[k] for k in (
        "early_silence_count", "no_greeting_silence_count", "total_calls")}))


if __name__ == "__main__":
    main()
