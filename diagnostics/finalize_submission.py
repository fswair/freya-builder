"""Build once, publish and export a submission package without starting any calls.

Run with ``uv run --locked python diagnostics/finalize_submission.py --help``.
Only explicit brief/API/reference inputs are read; no dataset or holdout is discovered.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import math
import os
import re
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any

import httpx
from dotenv import load_dotenv

from freya_hackathon.builder import KIT, ROOT, Builder, load_inputs, utc_now, write_json
from freya_hackathon.export_parity import OFFICIAL_LLM, require_preserved
from freya_hackathon.recording import capture_sync


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_fresh_trace(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    starts = [row for row in rows if row.get("event") == "builder_start"]
    ends = [row for row in rows if row.get("event") == "builder_end"]
    calls = [row for row in rows if row.get("event") == "model_call_start"]
    completed = [row for row in rows if row.get("event") == "model_call_end"]
    if (
        len(starts) != 1
        or len(ends) != 1
        or ends[0].get("status") != "locally_validated"
        or not calls
        or len(calls) != len(completed)
        or {row.get("call") for row in calls} != {row.get("call") for row in completed}
        or any(not row.get("time") for row in rows)
        or any(not row.get("model") or not row.get("purpose") for row in calls)
    ):
        raise ValueError("Fresh builder trace is incomplete")


class Publisher:
    """Isolated official kit module with a deadline-bound, workflow-only HTTP transport."""

    def __init__(
        self,
        kit: Path,
        deadline: float,
        *,
        clock: Callable[[], float] = monotonic,
        client: httpx.Client | None = None,
    ):
        if not os.environ.get("FREYA_API_KEY"):
            raise ValueError("FREYA_API_KEY is required")
        self.clock = clock
        self.deadline = deadline
        self.kit_path = (kit / "freya.py").resolve()
        spec = importlib.util.spec_from_file_location("submission_freya", self.kit_path)
        if spec is None or spec.loader is None:
            raise ValueError("Cannot load the official Freya client")
        self.freya: Any = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.freya)
        if self.freya.AGENT_LLM != OFFICIAL_LLM:
            raise ValueError("Runtime model must remain the official event model")
        self.client = client if client is not None else httpx.Client(timeout=45)
        self.freya.api = self.api

    def remaining(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError("Submission deadline exceeded")
        return remaining

    def api(self, method: str, path: str, body: Any = None, **params: Any) -> Any:
        allowed = (
            (method == "GET" and path == "/api/v2/workspaces")
            or (method == "POST" and path == "/api/v2/workflow")
            or (method in {"GET", "PUT"} and re.fullmatch(r"/api/v2/workflow/[A-Za-z0-9_-]+", path))
            or (method == "GET" and re.fullmatch(r"/api/v2/versions/workflow/[A-Za-z0-9_-]+", path))
            or (
                method == "POST"
                and re.fullmatch(r"/api/v2/versions/workflow/[A-Za-z0-9_-]+/publish", path)
            )
        )
        if not allowed:
            raise ValueError("Submission transport only allows workflow publication endpoints")
        response = self.client.request(
            method,
            self.freya.URL + path,
            json=body,
            params=params,
            headers=self.freya.HEADERS,
            timeout=min(45, self.remaining()),
        )
        self.remaining()
        if response.is_error:
            # Never embed response bodies or authentication material in exceptions/traces.
            raise RuntimeError(f"Freya workflow request failed with HTTP {response.status_code}")
        result = response.json()
        if isinstance(result, dict) and result.get("success") is False:
            raise RuntimeError("Freya rejected the workflow request")
        return result.get("data", {}).get("result", result) if isinstance(result, dict) else result

    def close(self) -> None:
        self.client.close()


def finalize_submission(
    builder: Builder,
    slot: str,
    *,
    kit: Path = KIT,
    state_file: Path = ROOT / ".freya-submission-agents.json",
    seconds: float = 1800,
    clock: Callable[[], float] = monotonic,
    publisher_factory: Callable[..., Any] = Publisher,
    started: tuple[float, str] | None = None,
) -> dict:
    """Fresh generation and publication under a single contiguous wall-clock budget."""
    if not math.isfinite(seconds) or not 0 < seconds <= 1800:
        raise ValueError("Submission budget must be in (0, 1800]")
    if not slot.strip():
        raise ValueError("A stable submission slot is required")
    if builder.output.exists():
        raise FileExistsError("Submission output must be a new directory")
    if builder.reuse_policy_from or builder.repair_from:
        raise ValueError("Submission requires fresh generation, without reuse or repair")
    started_clock, started_at = started if started is not None else (clock(), utc_now())
    if not math.isfinite(started_clock) or started_clock > clock():
        raise ValueError("Submission start time must be finite and not in the future")
    deadline = started_clock + seconds
    runner_path = Path(__file__).resolve()
    dependencies = [
        {"path": str(path), "sha256": digest(path)}
        for path in (runner_path, (kit / "freya.py").resolve())
    ]

    def action() -> dict:
        status = "failed"
        publisher = None
        deployment: dict = {}
        try:
            publisher = publisher_factory(kit, deadline, clock=clock)
            builder.seconds = min(builder.seconds, publisher.remaining())
            asyncio.run(builder.run())
            publisher.remaining()
            (builder.output / "submission-runner.txt").write_bytes(runner_path.read_bytes())
            workflow_path = builder.output / "workflow.json"
            generated_hash = digest(workflow_path)
            workflow = json.loads(workflow_path.read_text())
            scope = json.loads((builder.output / "execution-scope.json").read_text())
            if scope.get("reuses_prior_generation") or not scope.get("fresh_submission_run"):
                raise ValueError("Builder did not produce a fresh execution scope")
            require_fresh_trace(builder.output / "trace.jsonl")
            builder.trace("submission_publish_start", slot=slot)
            validation = publisher.freya.validate_workflow(slot, workflow)
            if validation.get("valid") is not True:
                raise ValueError("Freya dry-run validation rejected the workflow")
            write_json(builder.output / "submission-validation.json", {"valid": True})
            deployment = publisher.freya.save_workflow(slot, workflow, str(state_file))
            if "version" not in deployment:
                deployment["version"] = publisher.freya.update_workflow(
                    deployment["workflow_id"], workflow
                )
            versions = publisher.api(
                "GET",
                f"/api/v2/versions/workflow/{deployment['workflow_id']}",
                workspaceId=publisher.freya.workspace_id(),
            )["versions"]
            if not any(
                v["versionNumber"] == deployment["version"] and v.get("isPublished")
                for v in versions
            ):
                raise ValueError("Published workflow version was not confirmed")
            export_path = builder.output / "submission.workflow.json"
            publisher.freya.export_workflow(deployment["workflow_id"], str(export_path))
            exported = json.loads(export_path.read_text())
            if exported.get("workflow_id") != deployment["workflow_id"]:
                raise ValueError("Export workflow ID does not match the published workflow")
            if exported.get("agent_id") != deployment["agent_id"]:
                raise ValueError("Export agent ID does not match the published agent")
            require_preserved(workflow, exported["workflow"])
            runtime = exported["workflow"].get("agent_config", {}).get("llmConfig", {})
            if any(runtime.get(k) != v for k, v in publisher.freya.AGENT_LLM.items()):
                raise ValueError("Export does not use the official event runtime model")
            if digest(workflow_path) != generated_hash:
                raise ValueError("Generated workflow changed during publication")
            publisher.remaining()
            builder.trace(
                "submission_exported",
                **deployment,
                export_file=export_path.name,
                export_sha256=digest(export_path),
                generated_sha256=generated_hash,
            )
            status = "exported"
        finally:
            if publisher is not None:
                publisher.close()
            if builder.output.exists():
                elapsed = clock() - started_clock
                if elapsed >= seconds:
                    status = "failed"
                summary = {
                    "status": status,
                    "submission_ready": status == "exported",
                    "readiness_scope": "fresh workflow export and matching builder trace only",
                    "quality_evaluated": False,
                    "started_at": started_at,
                    "ended_at": utc_now(),
                    "budget_seconds": seconds,
                    "elapsed_seconds": elapsed,
                    "deployment": deployment,
                    "dependencies": dependencies,
                    "inputs": builder.inputs.manifest,
                    "mock_url": builder.inputs.mock_url,
                    "dataset_discovery": False,
                    "calls_started": 0,
                    "simulations_started": 0,
                }
                builder.trace("submission_end", **summary)
                summary["trace"] = {
                    "path": "trace.jsonl",
                    "sha256": digest(builder.output / "trace.jsonl"),
                }
                if status == "exported":
                    summary["export"] = {
                        "path": "submission.workflow.json",
                        "sha256": digest(builder.output / "submission.workflow.json"),
                    }
                write_json(builder.output / "submission.json", summary)
        if status != "exported":
            raise TimeoutError("Submission did not finish within its budget")
        return summary

    try:
        result = capture_sync(
            builder.output,
            "submission",
            action,
            inputs={"budget_seconds": seconds, "dependencies": dependencies},
            factors={"submission.calls_enabled": False, "submission.fresh_generation": True},
        )
        if clock() >= deadline:
            raise TimeoutError("Submission recording exceeded the total budget")
        return result
    except BaseException:
        manifest = builder.output / "submission.json"
        if manifest.exists():
            result = json.loads(manifest.read_text())
            result.update(status="failed", submission_ready=False)
            write_json(manifest, result)
        raise


def main() -> None:
    started = (monotonic(), utc_now())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("brief", type=Path)
    parser.add_argument("tool_api", type=Path, nargs="?",
                        help="Optional OpenAPI file; otherwise extract the supplied contract")
    parser.add_argument("--mock-url", help="Explicit deployed mock HTTPS base URL")
    parser.add_argument("--additional-brief", type=Path, action="append", default=[])
    parser.add_argument("--workflow-reference", type=Path, action="append", default=[])
    parser.add_argument("--guidance", type=Path)
    parser.add_argument("--kit", type=Path, default=KIT)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--slot", required=True)
    parser.add_argument("--state-file", type=Path, default=ROOT / ".freya-submission-agents.json")
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--reasoning", choices=("low", "medium", "high", "xhigh"), default="high")
    parser.add_argument("--seconds", type=float, default=1800, help="Total contiguous budget")
    parser.add_argument("--build-seconds", type=float, default=1200)
    parser.add_argument("--call-seconds", type=float, default=600)
    parser.add_argument("--attempts", type=int, default=2)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    try:
        inputs = load_inputs(
            [args.brief, *args.additional_brief],
            args.tool_api,
            args.kit,
            workflow_references=args.workflow_reference,
            mock_url=args.mock_url,
        )
        builder = Builder(
            inputs,
            args.out,
            model=args.model,
            reasoning=args.reasoning,
            seconds=args.build_seconds,
            call_seconds=args.call_seconds,
            attempts=args.attempts,
            guidance=args.guidance.read_text() if args.guidance else "",
        )
        finalize_submission(
            builder,
            args.slot,
            kit=args.kit,
            state_file=args.state_file,
            seconds=args.seconds,
            started=started,
        )
    except Exception as exc:
        # Provider errors can contain request bodies. Report only the error category.
        parser.exit(1, f"Submission failed ({type(exc).__name__}); inspect the run artifacts.\n")
    print(f"Export and trace package ready: {args.out / 'submission.json'}")


if __name__ == "__main__":
    main()
