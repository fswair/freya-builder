"""Autobench execution capture and offline case records for Freya evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import inspect
import json
import os
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from autobench import (
    BenchmarkSpec,
    Case,
    Direction,
    FactorValue,
    ObservationRole,
    RunContext,
    RunStatus,
    Variant,
    load_benchmark_spec,
    record_experiment,
    run_benchmark_spec,
)
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "benchmarks"
CURRENT: ContextVar[RunContext | None] = ContextVar("freya_autobench_context", default=None)


@dataclass
class Operation:
    kind: str
    directory: Path
    action: Callable[[], Any]
    result: Any = None
    error: BaseException | None = None


OPERATION: ContextVar[Operation] = ContextVar("freya_autobench_operation")


class CapturedOperationError(Exception):
    """Safe record message; the caller receives the original exception object."""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def secrets_to_redact() -> set[str]:
    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    secrets = {
        value
        for key, value in values.items()
        if value and len(value) >= 8 and key.endswith(("_KEY", "_TOKEN", "_SECRET"))
    }
    key = ROOT / ".freya-team-key"
    if key.exists():
        secrets.add(key.read_text().strip())
    return secrets - {""}


def redact(value: Any, secrets: set[str]) -> Any:
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if key.lower().replace("-", "_")
            in {"authorization", "x_team_key", "api_key", "access_token", "refresh_token"}
            else redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets) for item in value]
    return value


def emit(event: str, **fields: Any) -> None:
    if ctx := CURRENT.get():
        ctx.event(event, redact(fields, secrets_to_redact()))


def attach_files(ctx: RunContext, directory: Path, paths: list[Path]) -> None:
    """Copy sanitized evidence into the record, never just retain a mutable file link."""
    manifest = []
    secrets = secrets_to_redact()
    seen: dict[str, str] = {}
    for path in sorted(set(paths), key=lambda p: (len(p.parts), str(p))):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.resolve().relative_to(directory.resolve()).as_posix()
        raw = path.read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        if source_hash in seen:
            manifest.append(
                {"path": relative, "source_sha256": source_hash, "artifact": seen[source_hash]}
            )
            continue
        text = raw.decode("utf-8")
        try:
            value = redact(json.loads(text), secrets)
            media_type = "application/json"
        except ValueError:
            value = redact(text, secrets)
            media_type = "text/plain"
        ctx.artifact(relative, value, media_type=media_type)
        seen[source_hash] = relative
        manifest.append({"path": relative, "source_sha256": source_hash, "artifact": relative})
    ctx.artifact("evidence-manifest", manifest, media_type="application/json")


def snapshot(ctx: RunContext, directory: Path) -> None:
    paths = [p for p in directory.glob("*") if p.suffix in {".json", ".jsonl", ".txt"}]
    for name in ("practice", "provenance", "candidates", "evaluations", "reflections", "data"):
        paths.extend(
            p for p in (directory / name).rglob("*") if p.suffix in {".json", ".jsonl", ".txt"}
        )
    attach_files(ctx, directory, paths)


def variant(directory: Path, origin: str, factors: dict[str, Any] | None = None) -> Variant:
    values = {
        "source.directory": str(directory.resolve()),
        "evidence.origin": origin,
        **(factors or {}),
    }
    if (directory / "workflow.json").exists():
        values["workflow.sha256"] = digest(directory / "workflow.json")
    return Variant(
        id=directory.name,
        factors=[FactorValue(name=key, value=value) for key, value in values.items()],
    )


def configured_spec(name: str, cases: list[Case], variants: list[Variant]) -> BenchmarkSpec:
    base = load_benchmark_spec(SPECS / f"freya-{name}.yaml")
    return BenchmarkSpec.model_validate(
        base.model_copy(
            update={
                "dataset": base.dataset.model_copy(update={"cases": cases}),
                "variants": variants,
            }
        ).model_dump()
    )


def record_path(directory: Path, kind: str) -> Path:
    return directory / "autobench" / f"{kind}-{uuid4().hex}"


async def execution_task(ctx: RunContext, _case: Case) -> dict:
    operation = OPERATION.get()
    token = CURRENT.set(ctx)
    try:
        with ctx.span(f"freya.{operation.kind}", kind="workflow"):
            if inspect.iscoroutinefunction(operation.action):
                operation.result = await operation.action()
            else:
                operation.result = await asyncio.to_thread(operation.action)
            if isinstance(operation.result, dict) and operation.result.get("total"):
                ctx.metric(
                    "freya.case_pass_rate",
                    operation.result["passed"] / operation.result["total"],
                    semantic_type="quality.correctness",
                    direction=Direction.MAXIMIZE,
                    role=ObservationRole.OBJECTIVE,
                )
            ctx.event("execution_completed", True)
            return {"completed": True}
    except BaseException as exc:
        operation.error = exc
        # Do not copy an HTTP exception body or credential-bearing error text into records.
        raise CapturedOperationError(type(exc).__name__) from None
    finally:
        try:
            snapshot(ctx, operation.directory)
        finally:
            CURRENT.reset(token)


async def capture_async(
    directory: Path,
    kind: str,
    action: Callable[[], Any],
    *,
    factors: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
) -> Any:
    if ctx := CURRENT.get():
        with ctx.span(f"freya.{kind}", kind="workflow"):
            return await action()
    operation = Operation(kind, directory.resolve(), action)
    spec = configured_spec(
        "execution",
        [Case(id=kind, input=inputs or {}, expected={"completed": True})],
        [variant(directory, "live", factors)],
    )
    destination = record_path(directory, kind)
    token = OPERATION.set(operation)
    try:
        experiment = await run_benchmark_spec(spec, concurrency_limit=1)
        record_experiment(
            experiment,
            destination,
            source_files=[*Path(__file__).parent.glob("*.py"), SPECS / "freya-execution.yaml"],
            path_root=ROOT,
        )
        if operation.error is not None:
            raise operation.error
        if any(run.status != RunStatus.PASSED for run in experiment.runs):
            raise RuntimeError(f"Autobench execution failed; inspect {destination}")
        return operation.result
    except BaseException as exc:
        if operation.error is not None and exc is not operation.error:
            operation.error.add_note(f"Autobench recording also failed: {type(exc).__name__}")
            raise operation.error from exc
        raise
    finally:
        OPERATION.reset(token)


def capture_sync(
    directory: Path,
    kind: str,
    action: Callable[[], Any],
    *,
    factors: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
) -> Any:
    if ctx := CURRENT.get():
        with ctx.span(f"freya.{kind}", kind="workflow"):
            return action()
    return asyncio.run(capture_async(directory, kind, action, factors=factors, inputs=inputs))


def runtime_events(call: dict) -> list[dict]:
    events = []
    for message in (call.get("call") or call).get("messages", []):
        if message.get("role") == "system":
            try:
                event = json.loads(message.get("message", ""))
            except (ValueError, TypeError):
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def case_task(ctx: RunContext, case: Case) -> dict:
    directory = Path(str(ctx.factor("source.directory")))
    practice = directory / "practice"
    report = json.loads((practice / "results.json").read_text())
    row = next(row for row in report["rows"] if row["id"] == case.id)
    files = [directory / "workflow.json", directory / "policy.json", practice / "results.json"]
    files.extend(practice / name for name in ("deployment.json", "runtime-health.json"))
    events = []
    transcript_available = False
    for field, prefix in (("call_id", "call"), ("customer_id", "ledger")):
        if identifier := row.get(field):
            path = practice / f"{prefix}-{identifier}.json"
            if path.resolve().parent != practice.resolve():
                raise ValueError("Invalid evidence file identifier")
            files.append(path)
            if prefix == "call" and path.exists():
                transcript_available = True
                events = runtime_events(json.loads(path.read_text()))
    failures = [e for e in events if e.get("event") == "function_call_error"]
    snekbox = sum("Failed to reach snekbox" in str(e.get("error")) for e in failures)
    ctx.metric(
        "freya.snekbox_errors",
        snekbox,
        direction=Direction.MINIMIZE,
        role=ObservationRole.DIAGNOSTIC,
    )
    ctx.metric(
        "freya.extraction_warnings",
        sum(e.get("event") in {"extraction_failed", "extraction_error"} for e in events),
        direction=Direction.MINIMIZE,
        role=ObservationRole.DIAGNOSTIC,
    )
    ctx.metric(
        "freya.variable_validation_errors",
        sum(
            len(e["result"]["validation_errors"])
            for e in events
            if e.get("event") == "function_call_complete"
            and isinstance(e.get("result"), dict)
            and isinstance(e["result"].get("validation_errors"), list)
        ),
        direction=Direction.MINIMIZE,
        role=ObservationRole.DIAGNOSTIC,
    )
    ctx.metric("freya.transcript_available", transcript_available, role=ObservationRole.DIAGNOSTIC)
    row = redact(row, secrets_to_redact())
    ctx.artifact("official-case-result", row, media_type="application/json")
    attach_files(ctx, directory, files)
    return {"passed": row["pass"], "failed": row["failed"], "snekbox_errors": snekbox}


def export_cases(directories: list[Path], destination: Path, *, origin: str = "imported") -> Path:
    """Build case x candidate records from stored official outcomes, with no network calls."""
    if destination.exists():
        raise FileExistsError(destination)
    definitions = []
    for directory in directories:
        cases = json.loads((directory / "practice" / "cases.json").read_text())
        definitions.append({case["id"]: case for case in cases})
    if any(definition != definitions[0] for definition in definitions[1:]):
        raise ValueError("Comparisons require identical selected practice cases")
    if len({directory.name for directory in directories}) != len(directories):
        raise ValueError("Candidate directory names must be unique")
    spec = configured_spec(
        "cases",
        [
            Case(id=key, input=value, expected={"passed": True})
            for key, value in definitions[0].items()
        ],
        [variant(directory, origin) for directory in directories],
    )
    experiment = asyncio.run(run_benchmark_spec(spec, concurrency_limit=1))
    record_experiment(
        experiment,
        destination,
        source_files=[*Path(__file__).parent.glob("*.py"), SPECS / "freya-cases.yaml"],
        path_root=ROOT,
    )
    if any(
        run.task_result.error is not None or run.status not in {RunStatus.PASSED, RunStatus.FAILED}
        for run in experiment.runs
    ):
        raise RuntimeError(f"Case evidence could not be imported; inspect {destination}")
    return destination


def record_cases(directory: Path, *, origin: str = "postprocessed-live") -> Path:
    return export_cases([directory], record_path(directory, "cases"), origin=origin)


def import_run(directory: Path) -> None:
    """Preserve historical artifacts without inventing historical native SDK spans."""
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    capture_sync(
        directory,
        "import",
        lambda: {"imported": True},
        factors={"evidence.origin": "imported", "native_capture_available": False},
    )
    if (directory / "practice" / "results.json").exists():
        record_cases(directory, origin="imported")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    importer = sub.add_parser("import", help="Import old evidence without rerunning subjects")
    importer.add_argument("runs", nargs="+", type=Path)
    cases = sub.add_parser("cases", help="Record and compare identical saved case sets")
    cases.add_argument("runs", nargs="+", type=Path)
    cases.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "import":
        for directory in args.runs:
            import_run(directory.resolve())
            print(directory / "autobench")
    else:
        print(export_cases([directory.resolve() for directory in args.runs], args.out))


if __name__ == "__main__":
    # Autobench resolves tasks by canonical module name; share its ContextVars in -m mode.
    importlib.import_module("freya_hackathon.recording").main()
