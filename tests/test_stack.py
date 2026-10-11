from __future__ import annotations

from pathlib import Path

import pytest
from autobench import build_report, record_experiment, replay_experiment, run_benchmark_path
from autobench.instrumentation.pydantic_gepa import EXTENSION_KEY, PydanticGEPAEvidence
from pydantic_ai import Agent, models

from freya_hackathon import live, smoke

ROOT = Path(__file__).resolve().parents[1]


def test_real_optimizer_agent_evidence_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    result = run_benchmark_path(ROOT / "benchmarks/smoke.yaml")
    assert len(result.runs) == 1
    run = result.runs[0]
    assert run.error is None, run.error
    outcome = smoke.SmokeOutcome.model_validate(run.task_result.output)
    assert outcome.candidate == smoke.TARGET
    assert outcome.score == 1.0
    assert 0 < outcome.evaluation_calls <= 6
    assert outcome.baseline_after_optimization == "unknown"
    report = build_report(result)
    assert not report.optimization_warnings
    assert len(report.optimizations) == 1
    assert report.optimizations[0].execution.final_score == 1.0
    assert run.trace is not None
    assert any(span.operation == "pydantic_ai.agent.run" for span in run.trace.spans)

    record_dir = tmp_path / "record"
    record_experiment(result, record_dir)

    def forbid_execution(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Replay must not execute the application")

    monkeypatch.setattr(smoke, "run", forbid_execution)
    replayed = replay_experiment(record_dir)
    assert smoke.SmokeOutcome.model_validate(replayed.runs[0].task_result.output) == outcome
    assert PydanticGEPAEvidence.model_validate(
        replayed.runs[0].extensions[EXTENSION_KEY]
    ) == PydanticGEPAEvidence.model_validate(run.extensions[EXTENSION_KEY])
    assert build_report(replayed).optimizations == report.optimizations


def test_repeated_candidate_evaluations_preserve_trace_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    result = run_benchmark_path(ROOT / "benchmarks/smoke.yaml")
    assert len(result.runs) == 1
    run = result.runs[0]
    assert run.error is None
    assert run.trace is not None
    assert not run.trace.diagnostics


def test_live_access_failure_stops_before_optimization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    attempts = 0

    async def reject_access(
        _session: live.LiveSession, _agent: Agent[None, str], _prompt: str, _fallback: str
    ) -> str:
        nonlocal attempts
        attempts += 1
        raise PermissionError("Model is unavailable for this test account")

    monkeypatch.setattr(live.LiveSession, "ask", reject_access)
    result = run_benchmark_path(live.SPEC)
    assert len(result.runs) == 1
    run = result.runs[0]
    assert run.error is not None
    assert attempts == 1
    assert run.trace is not None
    assert all(span.operation != "pydantic_gepa.optimization" for span in run.trace.spans)
