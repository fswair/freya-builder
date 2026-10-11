"""Run generation and optional Freya practice under one wall-clock budget."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any

from freya_hackathon.builder import Builder, utc_now, write_json
from freya_hackathon.practice import Practice
from freya_hackathon.recording import capture_sync


def run_pipeline(
    builder: Builder,
    cases: Path,
    brief: str,
    slot: str,
    seconds: float = 1800,
    *,
    practice_factory: Callable[[Path, float], Any] = Practice,
    clock: Callable[[], float] = monotonic,
) -> dict:
    if not 0 < seconds <= 1800:
        raise ValueError("Pipeline budget must be in (0, 1800]")
    if builder.output.exists():
        raise FileExistsError("Pipeline output must be a new directory")
    return capture_sync(
        builder.output,
        "pipeline",
        lambda: _run_pipeline(builder, cases, brief, slot, seconds, practice_factory, clock),
        factors={"brief": brief, "freya.runtime_model": "experimental-5"},
        inputs={"seconds": seconds},
    )


def _run_pipeline(
    builder: Builder,
    cases: Path,
    brief: str,
    slot: str,
    seconds: float,
    practice_factory: Callable[[Path, float], Any],
    clock: Callable[[], float],
) -> dict:
    started, started_at = clock(), utc_now()
    builder.seconds = min(builder.seconds, seconds)
    status = "failed"
    try:
        generated = asyncio.run(builder.run())
        remaining = seconds - (clock() - started)
        if remaining <= 0:
            raise TimeoutError("No pipeline budget remains for practice")
        practice = practice_factory(builder.output, remaining)
        try:
            evaluated = practice.execute(cases, brief, slot)
        finally:
            practice.client.close()
        status = "evaluated"
        return {"generation": generated, "practice": evaluated}
    finally:
        if builder.output.exists():
            summary = {
                "started_at": started_at,
                "ended_at": utc_now(),
                "status": status,
                "budget_seconds": seconds,
                "elapsed_seconds": clock() - started,
            }
            write_json(builder.output / "pipeline.json", summary)
            builder.trace("pipeline_end", **summary)
