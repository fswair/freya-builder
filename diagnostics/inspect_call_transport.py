"""Record response metadata for an existing call without starting a simulation."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from uuid import UUID

from freya_hackathon.builder import write_json
from freya_hackathon.practice import Practice
from freya_hackathon.recording import capture_sync, redact, secrets_to_redact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("call_id", type=UUID)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def inspect() -> dict:
        practice = Practice(args.out, seconds=90)
        try:
            response = practice.client.get(
                practice.freya.URL + f"/api/v2/call/{args.call_id}",
                headers=practice.freya.HEADERS,
                timeout=practice.remaining(),
            )
            report = {
                "call_id": str(args.call_id),
                "status_code": response.status_code,
                "response_bytes": len(response.content),
                "response_sha256": hashlib.sha256(response.content).hexdigest(),
                "error_body": (
                    redact(response.text, secrets_to_redact())
                    if response.is_error and len(response.content) <= 4096
                    else None
                ),
            }
            write_json(args.out / "transport.json", report)
            return report
        finally:
            practice.client.close()

    print(capture_sync(args.out, "call_transport", inspect))


if __name__ == "__main__":
    main()
