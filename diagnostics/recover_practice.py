"""Collect an existing synthetic simulation into a new evidence directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

import httpx
from training_first_gepa import ReadResilientPractice

from freya_hackathon.builder import utc_now, write_json
from freya_hackathon.optimization import workspace_lock
from freya_hackathon.recording import capture_sync


class ServiceWaitingPractice(ReadResilientPractice):
    """Keep exhausted transient GET reads inside the existing total recovery deadline."""

    def api(self, method, path, body=None, **params):
        if method.upper() != "GET":
            return super().api(method, path, body, **params)
        exhausted_groups = 0
        while True:
            self.remaining()
            try:
                return super().api(method, path, body, **params)
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                transient = isinstance(exc, httpx.TransportError) or exc.response.status_code in {
                    429,
                    500,
                    502,
                    503,
                    504,
                }
                if not transient:
                    raise
                exhausted_groups += 1
                self.trace(
                    "practice_read_wait",
                    path=path,
                    exhausted_groups=exhausted_groups,
                    error=type(exc).__name__,
                )
                time.sleep(min(10.0, self.remaining()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--seconds", type=float, default=900)
    parser.add_argument("--wait-for-service", action="store_true")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("seconds must be positive")
    with workspace_lock():
        args.out.mkdir(parents=True, exist_ok=False)
        (args.out / "practice").mkdir()
        copied = {}
        for source in [
            *args.source.glob("*.json"),
            *args.source.joinpath("practice").glob("*.json"),
        ]:
            relative = source.relative_to(args.source)
            if relative.name in {"results.json", "runtime-health.json"}:
                continue
            shutil.copyfile(source, args.out / relative)
            copied[str(relative)] = hashlib.sha256(source.read_bytes()).hexdigest()
        write_json(
            args.out / "recovery-source.json",
            {
                "started_at": utc_now(),
                "source": str(args.source.resolve()),
                "files": copied,
                "new_simulation": False,
                "original_evidence_modified": False,
                "budget_seconds": args.seconds,
                "wait_for_service": args.wait_for_service,
            },
        )

        def recover():
            practice_type = (
                ServiceWaitingPractice if args.wait_for_service else ReadResilientPractice
            )
            practice = practice_type(args.out, args.seconds, resume=True)
            try:
                return practice.collect()
            finally:
                practice.client.close()

        result = capture_sync(args.out, "existing_simulation_recovery", recover)
        print(json.dumps({"passed": result["passed"], "total": result["total"]}))


if __name__ == "__main__":
    main()
