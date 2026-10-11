"""Offline integration fixture, not a hackathon agent or a quality benchmark."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from autobench import Case, RunContext
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_gepa import AgentInstructionsInjection, Component, Example, GEPAConfig, Optimization
from pydantic_gepa.configuration import BudgetConfig, ReflectionConfig
from pydantic_gepa.values import SerializableValue

BASELINE = "Return unknown."
TARGET = "Return ready."


class SmokeOutcome(BaseModel):
    candidate: str
    score: float
    evaluation_calls: int
    baseline_after_optimization: str


def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Make instruction changes observable without network access or credentials."""
    answer = "ready" if info.instructions == TARGET else "unknown"
    return ModelResponse(parts=[TextPart(answer)])


def propose(
    candidate: dict[str, str],
    _evidence: Mapping[str, Sequence[Mapping[str, SerializableValue]]],
    components_to_update: list[str],
) -> dict[str, str]:
    if "instructions" in components_to_update:
        return {**candidate, "instructions": TARGET}
    return candidate


async def run(_ctx: RunContext, _case: Case) -> SmokeOutcome:
    agent = Agent(FunctionModel(respond), instructions=BASELINE, output_type=str)
    instructions = Component(name="instructions", initial_text=BASELINE, kind="instructions")

    async def task(text: str) -> str:
        return (await agent.run(text)).output

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
            budget=BudgetConfig(max_metric_calls=6),
        ),
    )
    return SmokeOutcome(
        candidate=instructions.decode(result.best_candidate.values["instructions"]),
        score=result.best_score,
        evaluation_calls=result.total_metric_calls or 0,
        baseline_after_optimization=(await agent.run("check isolation")).output,
    )
