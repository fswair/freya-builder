"""Opt-in Codex smoke: real model evaluations and reflection, followed by offline replay."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic

from autobench import (
    Case,
    RunContext,
    build_report,
    record_experiment,
    replay_experiment,
    run_benchmark_path,
)
from autobench.instrumentation.pydantic_gepa import EXTENSION_KEY, PydanticGEPAEvidence
from codex_auth_helper import CodexAuthConfig, create_codex_responses_model
from pydantic import BaseModel
from pydantic_ai import Agent, models
from pydantic_ai.usage import UsageLimits
from pydantic_gepa import AgentInstructionsInjection, Component, Example, GEPAConfig, Optimization
from pydantic_gepa.configuration import BudgetConfig, ReflectionConfig
from pydantic_gepa.values import SerializableValue

BASELINE = "Reply with only the lowercase word unknown."
REFLECT = (
    "Improve an agent's instruction using its evaluation evidence. "
    "Return only the replacement instruction, without quotes or explanation. "
    "Keep it under 40 words."
)
MAX_REQUESTS = 10
MAX_METRIC_CALLS = 6
CALL_TIMEOUT = 30.0
RUN_SECONDS = 180.0
SPEC = Path(__file__).resolve().parents[2] / "benchmarks/live.yaml"


@dataclass
class LiveSession:
    model_name: str
    calls: int = 0
    deadline: float = field(default_factory=lambda: monotonic() + RUN_SECONDS)
    failed: bool = False

    def should_stop(self, _state: object) -> bool:
        return self.failed or self.calls >= MAX_REQUESTS or monotonic() >= self.deadline

    async def ask(self, agent: Agent[None, str], prompt: str, fallback: str) -> str:
        remaining = self.deadline - monotonic()
        if self.failed or self.calls >= MAX_REQUESTS or remaining <= 0:
            raise RuntimeError("Live smoke request or time budget exhausted")
        self.calls += 1
        # GEPA may evaluate in distinct event loops. Own and close each async client
        # inside the loop making the request, rather than sharing its connection pool.
        model = create_codex_responses_model(
            self.model_name,
            instructions=fallback,
            config=CodexAuthConfig(timeout_seconds=CALL_TIMEOUT),
            settings={"timeout": CALL_TIMEOUT, "openai_reasoning_effort": "low"},
        )
        model.client.max_retries = 0
        try:
            async with asyncio.timeout(min(CALL_TIMEOUT, remaining)), model.responses_turn():
                result = await agent.run(
                    prompt, model=model, usage_limits=UsageLimits(request_limit=1)
                )
                return result.output.strip()
        except Exception:
            self.failed = True
            raise
        finally:
            await model.client.close()


class LiveOutcome(BaseModel):
    model_name: str
    candidate: str
    score: float
    evaluation_calls: int
    model_calls: int
    reflection_calls: int
    baseline_output: str
    baseline_after_optimization: str
    held_out_output: str


async def run(ctx: RunContext, _case: Case) -> LiveOutcome:
    session = LiveSession(model_name=str(ctx.factor("model.name")))
    agent = Agent(instructions=BASELINE, output_type=str, retries=0)
    reflector = Agent(instructions=REFLECT, output_type=str, retries=0)
    instructions = Component(name="instructions", initial_text=BASELINE, kind="instructions")
    reflection_calls = 0
    # Check actual inference access before GEPA's evaluation/reflection retry paths.
    baseline_output = await session.ask(agent, "access probe", BASELINE)

    async def task(text: str) -> str:
        return await session.ask(agent, text, BASELINE)

    def propose(
        candidate: dict[str, str],
        evidence: Mapping[str, Sequence[Mapping[str, SerializableValue]]],
        components_to_update: list[str],
    ) -> dict[str, str]:
        nonlocal reflection_calls
        if "instructions" not in components_to_update:
            return candidate
        reflection_calls += 1
        prompt = (
            "The task requires the exact lowercase output ready for every probe.\n"
            f"Current instruction: {candidate['instructions']}\n"
            f"Evaluation evidence: {evidence}\n"
            "Write the replacement instruction."
        )
        proposed = asyncio.run(session.ask(reflector, prompt, REFLECT))
        return {**candidate, "instructions": proposed}

    optimization = Optimization[str, str, None].from_examples(
        examples=[Example(name="train", inputs="training probe", expected_output="ready")],
        val_examples=[
            Example(name="validation", inputs="validation probe", expected_output="ready")
        ],
        task=task,
        score=lambda ctx: float(ctx.output == ctx.expected_output),
        components=[instructions],
        injections=[AgentInstructionsInjection(agent=agent, candidate_component=instructions)],
    )
    result = await asyncio.to_thread(
        optimization.optimize,
        config=GEPAConfig(
            reflection=ReflectionConfig(proposer=propose, minibatch_size=1),
            budget=BudgetConfig(max_metric_calls=MAX_METRIC_CALLS, stop=(session.should_stop,)),
        ),
    )
    candidate = instructions.decode(result.best_candidate.values["instructions"])
    baseline_after = await session.ask(agent, "isolation probe", BASELINE)
    deployed_agent = Agent(instructions=candidate, output_type=str, retries=0)
    held_out = await session.ask(deployed_agent, "held-out probe", candidate)
    return LiveOutcome(
        model_name=session.model_name,
        candidate=candidate,
        score=result.best_score,
        evaluation_calls=result.total_metric_calls or 0,
        model_calls=session.calls,
        reflection_calls=reflection_calls,
        baseline_output=baseline_output,
        baseline_after_optimization=baseline_after,
        held_out_output=held_out,
    )


def verify_record(record: Path) -> dict[str, object]:
    """Verify recorded evidence without permitting another model request."""
    with models.override_allow_model_requests(False):
        result = replay_experiment(record)
        report = build_report(result)
    if len(result.runs) != 1:
        raise RuntimeError("Expected exactly one live smoke run")
    run = result.runs[0]
    if run.error is not None:
        raise RuntimeError("Live benchmark failed; inspect the recorded run error")
    outcome = LiveOutcome.model_validate(run.task_result.output)
    if outcome.score != 1.0 or outcome.held_out_output != "ready":
        raise RuntimeError("Optimized candidate did not pass validation and the held-out probe")
    if outcome.baseline_output != "unknown" or outcome.baseline_after_optimization != "unknown":
        raise RuntimeError("Baseline behavior or injection isolation check failed")
    if not 0 < outcome.evaluation_calls <= MAX_METRIC_CALLS:
        raise RuntimeError("GEPA metric budget check failed")
    if not 0 < outcome.reflection_calls < outcome.model_calls <= MAX_REQUESTS:
        raise RuntimeError("Real reflection or model request budget check failed")
    if run.trace is None or run.trace.diagnostics or report.optimization_warnings:
        raise RuntimeError("Live trace is missing or contains diagnostics")
    operations = {span.operation for span in run.trace.spans}
    required = {"pydantic_ai.agent.run", "pydantic_gepa.optimization", "openai.responses"}
    if not required <= operations:
        raise RuntimeError(f"Missing native trace operations: {sorted(required - operations)}")
    evidence = PydanticGEPAEvidence.model_validate(run.extensions[EXTENSION_KEY])
    if len(evidence.executions) != 1 or evidence.executions[0].final_score != outcome.score:
        raise RuntimeError("Recorded optimizer evidence does not match the outcome")
    return {
        **outcome.model_dump(),
        "record": str(record),
        "diagnostics": len(run.trace.diagnostics),
        "span_count": len(run.trace.spans),
        "operations": sorted(operations),
        "offline_replay": "passed",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--replay-only", action="store_true")
    args = parser.parse_args()
    if not args.replay_only:
        if args.record.exists():
            parser.error("Choose a new record directory before making live requests")
        result = run_benchmark_path(SPEC)
        record_experiment(result, args.record)
    print(json.dumps(verify_record(args.record), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
