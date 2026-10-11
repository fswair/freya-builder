"""Network-free GEPA -> real Builder/Pydantic AI -> fake Freya -> Autobench check."""

from __future__ import annotations

import argparse
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from autobench import build_report, replay_experiment
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from freya_hackathon.builder import KIT, Builder, write_json
from freya_hackathon.optimization import run_study
from freya_hackathon.optimization_data import StudyConfig
from freya_hackathon.policy import Evidence, Policy, Rule

INITIAL = "Use compact steps."
IMPROVED = "Keep an explicit consent guard after a correction."
MODEL_INSTRUCTIONS: list[str] = []


def omit_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: omit_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [omit_nulls(v) for v in value]
    return value


@asynccontextmanager
async def fake_model(_name: str, _seconds: float, _reasoning: str):
    def respond(_messages, info: AgentInfo):
        instructions = info.instructions or ""
        MODEL_INSTRUCTIONS.append(instructions)
        if instructions.startswith("Review the supplied candidate"):
            output = {"findings": [], "edits": [], "replacement": None, "unresolved": []}
        elif "Return WorkflowBuild:" in instructions:
            workflow = omit_nulls(json.loads((KIT / "examples/base-agent.json").read_text()))
            workflow["nodes"]["identity"]["task_message"] = (
                "Request consent." if IMPROVED in instructions else "Proceed immediately."
            )
            output = {"workflow": workflow, "rule_nodes": {"consent": ["identity"]}}
        else:
            output = Policy(
                rules=[
                    Rule(
                        id="consent",
                        requirement="Request consent after corrections.",
                        applies_when="After a correction",
                        implementation="Conversation consent gate",
                        evidence=[
                            Evidence(source="brief-1", quote="Request consent after corrections.")
                        ],
                    )
                ]
            ).model_dump()
        return ModelResponse(parts=[TextPart(json.dumps(output))])

    yield FunctionModel(respond)


def fake_builder(*args, **kwargs):
    return Builder(*args, **kwargs, model_factory=fake_model)


@asynccontextmanager
async def fake_reflection_model(_name: str, _seconds: float, _reasoning: str):
    yield FunctionModel(
        lambda _messages, _info: ModelResponse(parts=[TextPart(json.dumps({"text": IMPROVED}))])
    )


class FakePractice:
    """An HTTPX mock scoring one fixture field; NOT a Freya quality evaluation."""

    def __init__(self, run: Path, _seconds: float):
        self.run = run
        self.client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"fixture": True})
            )
        )

    def execute(self, cases: Path, _brief: str, _slot: str):
        self.client.get("https://synthetic-fixture.invalid/evaluate")
        workflow = json.loads((self.run / "workflow.json").read_text())
        passed = workflow["nodes"]["identity"]["task_message"] == "Request consent."
        rows = json.loads(cases.read_text())
        output = self.run / "practice"
        output.mkdir()
        report = {
            "status": "completed",
            "total": len(rows),
            "passed": len(rows) if passed else 0,
            "rows": [
                {
                    "id": row["id"],
                    "pass": passed,
                    "failed": [] if passed else ["Fixture requires a consent guard"],
                }
                for row in rows
            ],
        }
        write_json(output / "results.json", report)
        write_json(
            output / "runtime-health.json",
            {
                "infrastructure_affected_cases": [],
                "unresolved_runtime_error_cases": [],
                "simulator_error_cases": [],
                "incomplete_evidence_cases": [],
                "usable_as_clean_model_comparison": True,
            },
        )
        return report


def prepare(directory: Path) -> StudyConfig:
    directory.mkdir(parents=True, exist_ok=False)
    dataset = directory / "dataset"
    dataset.mkdir()
    seed = json.loads(
        (
            KIT.parent.parent / "documents/dataset-request-v2/sources/public-test-set.json"
        ).read_text()
    )[0]
    write_json(directory / "seeds.json", [seed])
    for split in ("train", "validation"):
        case = {
            **seed,
            "id": f"DEV-{split}",
            "practice_session_case": seed["id"],
            "script": f"Synthetic {split} integration fixture; not a real customer simulation.",
            "metadata": {"split": split, "group_id": f"{split}-group"},
        }
        write_json(dataset / f"{split}.json", [case])
    # Deliberately invalid. Opening this during search is a bug, not a missing fixture.
    (dataset / "holdout.json").write_text("HOLDOUT MUST NOT BE READ DURING SEARCH")
    (directory / "brief.txt").write_text("Request consent after corrections.")
    (directory / "guidance.txt").write_text(INITIAL)
    write_json(
        directory / "study.json",
        {
            "dataset": "dataset",
            "public_cases": "seeds.json",
            "guidance": "guidance.txt",
            "briefs": {
                "A": {"documents": ["brief.txt"], "tool_api": str(KIT / "tools/openapi.json")}
            },
            "metric_calls": 6,
            "max_builds": 3,
            "validation_repetitions": 3,
            "study_seconds": 120,
        },
    )
    return StudyConfig.read(directory / "study.json")


def run(output: Path) -> dict:
    config = prepare(output / "fixture")
    MODEL_INSTRUCTIONS.clear()
    with models.override_allow_model_requests(False):
        selected = run_study(
            config,
            output / "study",
            builder_factory=fake_builder,
            practice_factory=FakePractice,
            evidence_scope="offline-integration",
            reflection_factory=fake_reflection_model,
        )
        record = next((output / "study/autobench").glob("optimization-*"))
        replayed = replay_experiment(record)
        build_report(replayed)
    trace = replayed.runs[0].trace
    operations = {span.operation for span in trace.spans} if trace else set()
    required = {"pydantic_gepa.optimization", "pydantic_ai.agent.run", "httpx.request"}
    if not required <= operations or selected["validation_score"] != 1.0:
        raise RuntimeError("Optimizer integration or native trace verification failed")
    if not any(IMPROVED in instructions for instructions in MODEL_INSTRUCTIONS):
        raise RuntimeError("Candidate guidance did not reach the real builder model")
    result = {
        "scope": "offline integration fixture; not Freya performance",
        "selected_guidance": (output / "study/selected-guidance.txt").read_text(),
        "validation_score": selected["validation_score"],
        "builds": selected["builds"],
        "native_operations": sorted(operations),
        "record": str(record),
        "offline_replay": "passed",
        "holdout_opened": False,
    }
    write_json(output / "smoke-results.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Choose a fresh smoke directory")
    print(json.dumps(run(args.out), indent=2))


if __name__ == "__main__":
    main()
