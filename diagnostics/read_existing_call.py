"""Read one known synthetic call with bounded GET retries; never start a call."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from training_first_gepa import ReadResilientPractice

from freya_hackathon.builder import utc_now, write_json
from freya_hackathon.recording import capture_sync, redact, secrets_to_redact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("call_id", type=UUID)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=120)
    args = parser.parse_args()
    if not 0 < args.seconds <= 180:
        parser.error("Call read budget must be between 0 and 180 seconds")
    args.out.mkdir(parents=True, exist_ok=False)

    def inspect() -> dict:
        practice = ReadResilientPractice(args.out, args.seconds)
        report = {"call_id": str(args.call_id), "new_call": False, "started_at": utc_now()}
        try:
            response = practice.api("GET", f"/api/v2/call/{args.call_id}")
            # Keep diagnostic evidence safe even if a remote variable table echoes a secret.
            response = redact(response, secrets_to_redact())
            write_json(practice.output / f"call-{args.call_id}.json", response)
            call = response.get("call", response)
            report.update(
                read_succeeded=True,
                ended=call.get("endedReason"),
                message_count=len(call.get("messages", [])),
            )
        except Exception as exc:
            report.update(read_succeeded=False, error_type=type(exc).__name__)
        finally:
            practice.client.close()
        report["finished_at"] = utc_now()
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "existing_call_read", inspect)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
