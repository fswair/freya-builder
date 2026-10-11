"""Recover selected synthetic call evidence using Freya's documented raw ZIP export."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.practice import Practice
from freya_hackathon.recording import capture_sync, redact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--case", action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--resume-response", type=Path)
    source.add_argument("--local-archive", type=Path)
    args = parser.parse_args()
    state = json.loads((args.run / "practice/simulation-state.json").read_text())
    results = json.loads((args.run / "practice/results.json").read_text())
    requested = set(args.case)
    rows = [row for row in results["rows"] if row["id"] in requested]
    if {row["id"] for row in rows} != requested or not rows:
        raise ValueError("Every requested case must exist in this synthetic simulation")
    call_ids = [row["call_id"] for row in rows]
    if not all(call_ids) or len(set(call_ids)) != len(call_ids):
        raise ValueError("Selected calls must have distinct nonempty call IDs")
    args.out.mkdir(parents=True, exist_ok=False)

    def export() -> dict:
        practice = Practice(args.out, seconds=180)
        try:
            secret_values = {
                value
                for name in ["FREYA_API_KEY", "FREYA_BANK_TEAM_KEY"]
                if (value := os.environ.get(name))
            }
            if args.local_archive:
                archive_bytes = args.local_archive.read_bytes()
                with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                    calls = json.loads(archive.read("calls.json"))
                if calls.get("sourceWorkspaceId") != state["workspaceId"]:
                    raise ValueError("Downloaded export belongs to another workspace")
                if {call["id"] for call in calls["calls"]} != set(call_ids):
                    raise ValueError("Downloaded export differs from selected calls")
                write_json(args.out / "calls.json", redact(calls, secret_values))
                write_json(
                    args.out / "manifest.json",
                    {
                        "source_run": str(args.run),
                        "cases": sorted(requested),
                        "call_ids": call_ids,
                        "source_archive": str(args.local_archive),
                        "export_sha256": hashlib.sha256(archive_bytes).hexdigest(),
                        "export_bytes": len(archive_bytes),
                        "source": "authenticated_browser_download",
                        "secrets_redacted": True,
                        "official_scores_modified": False,
                    },
                )
                return {"cases": sorted(requested), "export_bytes": len(archive_bytes)}
            if args.resume_response:
                envelope = json.loads(args.resume_response.read_text())
                job = envelope["data"]["result"]
                if job.get("callCount") != len(call_ids):
                    raise ValueError("Export job call count differs from selected calls")
                status_path = job["statusUrl"]
                if status_path != "/api/v2/export/" + job["jobId"]:
                    raise ValueError("Unexpected export status path")
                status = practice.api("GET", status_path)
                write_json(args.out / "export-status.json", status)
                if status.get("status") != "ready":
                    return {"cases": sorted(requested), "job_status_saved": True}
                # The returned downloadUrl is a browser page. The current OpenAPI
                # documents this authenticated file endpoint on the same API origin.
                response = practice.client.get(
                    practice.freya.URL + status_path + "/download",
                    headers=practice.freya.HEADERS,
                    timeout=practice.remaining(),
                )
                if response.is_error:
                    write_json(
                        args.out / "download-rejection.json",
                        {
                            "status_code": response.status_code,
                            "body": redact(response.text, secret_values),
                        },
                    )
                response.raise_for_status()
                content = response.content
                value = response.json()
                if not all(call_id in json.dumps(value) for call_id in call_ids):
                    raise ValueError("Downloaded export does not contain selected call IDs")
                write_json(args.out / "calls.json", redact(value, secret_values))
                write_json(
                    args.out / "manifest.json",
                    {
                        "source_run": str(args.run),
                        "cases": sorted(requested),
                        "call_ids": call_ids,
                        "job_id": job["jobId"],
                        "format": status.get("format"),
                        "row_count": status.get("rowCount"),
                        "export_bytes": len(content),
                        "export_sha256": hashlib.sha256(content).hexdigest(),
                        "secrets_redacted": True,
                        "official_scores_modified": False,
                    },
                )
                return {"cases": sorted(requested), "download_bytes": len(content)}
            response = practice.client.post(
                practice.freya.URL + "/api/v2/call/export-raw",
                headers=practice.freya.HEADERS,
                json={"workspaceId": state["workspaceId"], "callIds": call_ids},
                timeout=practice.remaining(),
            )
            response.raise_for_status()
            archive_bytes = response.content
            if not zipfile.is_zipfile(io.BytesIO(archive_bytes)):
                value = response.json()
                write_json(args.out / "export-response.json", redact(value, secret_values))
                return {
                    "cases": sorted(requested),
                    "http_status": response.status_code,
                    "archive_received": False,
                    "response_saved": True,
                }
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                calls = json.loads(archive.read("calls.json"))
                # Keep only call evidence; upstream workflow and recording exports stay in memory.
                write_json(args.out / "calls.json", redact(calls, secret_values))
                manifest = {
                    "source_run": str(args.run),
                    "cases": sorted(requested),
                    "call_ids": call_ids,
                    "export_sha256": hashlib.sha256(archive_bytes).hexdigest(),
                    "export_bytes": len(archive_bytes),
                    "entries": archive.namelist(),
                    "raw_archive_persisted": False,
                    "secrets_redacted": True,
                    "source": "https://docs.freyavoice.ai/api-reference/calls/raw-zip-export.md",
                    "official_scores_modified": False,
                }
                write_json(args.out / "manifest.json", manifest)
                return {"cases": sorted(requested), "export_bytes": len(archive_bytes)}
        finally:
            practice.client.close()

    print(
        json.dumps(
            capture_sync(
                args.out,
                "call_evidence_export",
                export,
                inputs={"cases": sorted(requested), "call_ids": call_ids},
            )
        )
    )


if __name__ == "__main__":
    main()
