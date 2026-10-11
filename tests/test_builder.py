from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest
from openai import APIConnectionError, APIError, APIStatusError
from pydantic_ai import models
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from freya_hackathon.adaptation import Replacement, WorkflowAdaptation
from freya_hackathon.builder import KIT, Builder, Inputs, load_inputs
from freya_hackathon.compiler import compile_workflow
from freya_hackathon.policy import Evidence, Policy, Rule
from freya_hackathon.repair import TextEdit, WorkflowTextRepair
from freya_hackathon.review import WorkflowReview
from freya_hackathon.workflow import WorkflowBuild, validate_workflow


@pytest.fixture
def policy() -> Policy:
    return Policy(
        rules=[
            Rule(
                id="identity",
                requirement="Check identity.",
                applies_when="Before account access",
                implementation="Identity node gate",
                evidence=[Evidence(source="brief-1", quote="Check identity.")],
            )
        ]
    )


@pytest.fixture
def api() -> dict[str, Any]:
    return json.loads((KIT / "tools/openapi.json").read_text())


@pytest.fixture
def draft() -> WorkflowBuild:
    # A kit fixture for structural tests only, not evidence of behavioral correctness.
    def omit_nulls(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: omit_nulls(v) for k, v in value.items() if v is not None}
        if isinstance(value, list):
            return [omit_nulls(v) for v in value]
        return value

    workflow = omit_nulls(json.loads((KIT / "examples/base-agent.json").read_text()))
    return WorkflowBuild(workflow=workflow, rule_nodes={"identity": ["identity"]})


def test_input_accepts_both_briefs_and_explicit_api() -> None:
    inputs = load_inputs(
        [KIT / "briefs/brief-a.md", KIT / "briefs/brief-b.md"], KIT / "tools/openapi.json"
    )
    assert set(inputs.sources) == {"brief-1", "brief-2", "tool-api"}
    assert len(inputs.manifest) == 6
    assert all(len(item["sha256"]) == 64 for item in inputs.manifest)


def test_locally_rejects_loop_prompt_rejected_by_freya_api(draft, policy, api):
    draft.workflow["nodes"]["identity"]["loop_prompt"] = "Ask only for missing information."
    with pytest.raises(ValueError, match="Freya API rejects loop_prompt"):
        validate_workflow(draft, policy, api)


def test_fabricated_evidence_and_cyclic_amendments_are_rejected(policy: Policy) -> None:
    with pytest.raises(ValueError, match="does not occur"):
        policy.check_sources({"brief-1": "Ask for payment immediately."})
    amended = policy.model_copy(deep=True)
    amended.rules.append(
        amended.rules[0].model_copy(update={"id": "amendment", "overrides": ["identity"]})
    )
    amended.rules[0].overrides = ["amendment"]
    with pytest.raises(ValueError, match="Cyclic"):
        amended.check_sources({"brief-1": "Check identity."})
    policy.rules[0].evidence[0].quote = "   "
    with pytest.raises(ValueError, match="does not occur"):
        policy.check_sources({"brief-1": "Check identity."})


def test_missing_authoritative_dependency_blocks_policy_and_adaptation(policy, draft):
    missing = "The governing baseline policy was referenced but not supplied."
    policy.unresolved = [missing]
    with pytest.raises(ValueError, match="unresolved requirements"):
        policy.check_sources({"brief-1": "Check identity."})
    reference = copy.deepcopy(draft.workflow)
    adaptation = WorkflowAdaptation(
        reference_id="example",
        adaptation_notes=["Source dependency report; unchanged example is not approved."],
        rule_nodes=draft.rule_nodes,
        unresolved=[missing],
    )
    with pytest.raises(ValueError, match="unresolved requirements"):
        adaptation.apply({"example": reference})
    assert reference == draft.workflow


def test_development_references_are_not_authoritative_policy_sources(tmp_path: Path, draft):
    reference = tmp_path / "reference.json"
    clean = {k: v for k, v in draft.workflow.items() if k not in {"test_variables", "agent_config"}}
    reference.write_text(json.dumps(clean))
    inputs = load_inputs(
        [KIT / "briefs/brief-a.md"],
        KIT / "tools/openapi.json",
        workflow_references=[reference],
    )
    assert set(inputs.sources) == {"brief-1", "tool-api"}
    assert inputs.workflow_references["development-reference-1"] == clean
    assert inputs.manifest[-1]["source"] == "development-reference-1"
    workflow = {**draft.workflow, "test_variables": {"customer_name": "fixture"}}
    reference.write_text(json.dumps(workflow))
    with pytest.raises(ValueError, match="Remove test variables"):
        load_inputs(
            [KIT / "briefs/brief-a.md"], KIT / "tools/openapi.json", workflow_references=[reference]
        )


def test_adaptation_preserves_reference_and_checks_structural_paths(draft):
    original = draft.model_copy(deep=True)
    old = draft.workflow["nodes"]["identity"]["task_message"]
    adaptation = WorkflowAdaptation(
        reference_id="reference",
        adaptation_notes=["Apply new source requirements"],
        edits=[
            TextEdit(
                path=["workflow", "nodes", "identity", "task_message"],
                before=old,
                after="Use the new identity questions.",
            )
        ],
        replacements=[Replacement(path=["workflow", "global_nodes"], value={})],
        rule_nodes={"new-policy-rule": ["identity"]},
    )
    result = adaptation.apply({"reference": draft.workflow})
    assert result.workflow["nodes"]["identity"]["task_message"] == "Use the new identity questions."
    assert result.rule_nodes == {"new-policy-rule": ["identity"]}
    assert draft == original
    for path in (
        ["workflow", "missing"],
        ["workflow", "nodes", "identity", "tools", -1],
        ["rule_nodes", "identity"],
    ):
        invalid = adaptation.model_copy(update={"replacements": [Replacement(path=path, value={})]})
        with pytest.raises(ValueError):
            invalid.apply({"reference": draft.workflow})
    with pytest.raises(ValueError, match="Unknown development"):
        adaptation.apply({})
    assert draft == original


@pytest.mark.parametrize("include_noop", [False, True])
def test_reference_builder_extracts_new_policy_and_records_adaptation(
    tmp_path, draft, policy, api, include_noop
):
    old = draft.workflow["nodes"]["identity"]["task_message"]
    adaptation = WorkflowAdaptation(
        reference_id="development-reference-1",
        adaptation_notes=["Identity node already implements this fixture's requirement"],
        edits=[
            TextEdit(path=["workflow", "nodes", "identity", "task_message"], before=old, after=old)
        ]
        if include_noop
        else [],
        rule_nodes={"identity": ["identity"]},
    )
    outputs = iter([policy.model_dump_json(), adaptation.model_dump_json()])

    @asynccontextmanager
    async def factory(_name: str, _seconds: float, _reasoning: str) -> AsyncGenerator[Model]:
        yield FunctionModel(lambda _messages, _info: ModelResponse(parts=[TextPart(next(outputs))]))

    inputs = Inputs(
        sources={"brief-1": "Check identity."},
        api=api,
        references="reference fixture",
        manifest=[],
        workflow_references={"development-reference-1": draft.workflow},
    )
    out = tmp_path / "adapted"
    asyncio.run(Builder(inputs, out, model_factory=factory).run())
    assert json.loads((out / "workflow.json").read_text()) == compile_workflow(draft)[0].workflow
    assert json.loads((out / "adaptation.json").read_text()) == adaptation.model_dump()
    scope = json.loads((out / "execution-scope.json").read_text())
    assert scope["fresh_submission_run"] is True
    assert scope["uses_public_development_references"] is True
    assert scope["reuses_prior_generation"] is False
    events = [json.loads(line) for line in (out / "trace.jsonl").read_text().splitlines()]
    assert [e["purpose"] for e in events if e["event"] == "model_call_start"] == [
        "policy",
        "workflow-adaptation",
    ]
    adapted = next(event for event in events if event["event"] == "workflow_adapted")
    assert adapted["edits"] == 0
    assert adapted["no_op_edits"] == int(include_noop)


@pytest.mark.parametrize(
    "problem",
    ["target", "external", "dropped", "uncovered", "null", "agent_model", "missing_close"],
)
def test_invalid_workflows_are_rejected(
    problem: str, draft: WorkflowBuild, policy: Policy, api: dict[str, Any]
) -> None:
    node = draft.workflow["nodes"]["identity"]
    if problem == "target":
        node["transitions"][0]["target_node"] = "missing"
    elif problem == "external":
        node["tools"][0]["function"]["parameters"]["properties"]["url"]["default"] = (
            "https://other.example/v1/verify_identity"
        )
    elif problem == "dropped":
        node["pre_actions"] = [node["tools"][0]]
    elif problem == "uncovered":
        draft.rule_nodes = {}
    elif problem == "null":
        node["max_turn_transition"] = None
    elif problem == "missing_close":
        draft.workflow["tools"] = []
    else:
        draft.workflow["agent_config"] = {}
    with pytest.raises(ValueError):
        validate_workflow(draft, policy, api)


def test_validation_uses_supplied_api_not_practice_field_values(
    draft: WorkflowBuild, policy: Policy, api: dict[str, Any]
) -> None:
    changed = copy.deepcopy(api)
    schema = changed["paths"]["/verify_identity"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    schema["properties"]["field"]["enum"] = ["security_word"]
    with pytest.raises(ValueError, match="unsupported enum"):
        validate_workflow(draft, policy, changed)
    prop = draft.workflow["nodes"]["identity"]["tools"][0]["function"]["parameters"]["properties"][
        "body"
    ]["properties"]["field"]
    prop["enum"] = ["security_word"]
    validate_workflow(draft, policy, changed)
    schema["required"].append("challenge_id")
    with pytest.raises(ValueError, match="missing required"):
        validate_workflow(draft, policy, changed)


def test_offline_builder_uses_real_pydantic_ai_and_records_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    draft: WorkflowBuild,
    policy: Policy,
    api: dict[str, Any],
) -> None:
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    original_message = draft.workflow["nodes"]["identity"]["task_message"]
    review = WorkflowReview(
        findings=["identity rule: clarify the privacy gate"],
        edits=[
            TextEdit(
                path=["workflow", "nodes", "identity", "task_message"],
                before=original_message,
                after=original_message + " Do not disclose an account before verification.",
            )
        ],
    )
    outputs = iter([policy.model_dump_json(), draft.model_dump_json(), review.model_dump_json()])

    def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(next(outputs))], usage=RequestUsage(cost=Decimal("0.1"))
        )

    @asynccontextmanager
    async def factory(_name: str, _seconds: float, _reasoning: str) -> AsyncGenerator[Model]:
        yield FunctionModel(respond)

    out = tmp_path / "build"
    inputs = Inputs(
        sources={"brief-1": "Check identity."}, api=api, references="fixture", manifest=[]
    )
    result = asyncio.run(Builder(inputs, out, model_factory=factory, review=True).run())
    assert result["freya_validation"] == "not_run"
    assert result["behavioral_evaluation"] == "not_run"
    compiled = compile_workflow(draft)[0]
    assert json.loads((out / "workflow.json").read_text()) == review.apply(compiled).workflow
    assert json.loads((out / "before-review.json").read_text()) == compiled.model_dump()
    assert json.loads((out / "review.json").read_text()) == review.model_dump()
    events = [json.loads(line) for line in (out / "trace.jsonl").read_text().splitlines()]
    assert [e["purpose"] for e in events if e["event"] == "model_call_start"] == [
        "policy",
        "workflow",
        "workflow-review",
    ]
    assert all(e["usage"]["cost"] == "0.1" for e in events if e["event"] == "model_call_usage")
    assert events[-1]["event"] == "builder_end"
    assert events[-1]["status"] == "locally_validated"
    with pytest.raises(FileExistsError):
        asyncio.run(Builder(inputs, out, model_factory=factory).run())


def provider_failure(kind: str) -> APIError | ModelHTTPError | TimeoutError:
    request = httpx2.Request("POST", "https://example.test/model")
    if kind == "connection":
        return APIConnectionError(request=request)
    if kind == "timeout":
        return TimeoutError("Provider response timed out")
    if kind.startswith("wrapped-"):
        return ModelHTTPError(int(kind.split("-")[1]), "fixture")
    if kind.isdigit():
        response = httpx2.Response(int(kind), request=request)
        return APIStatusError("Provider failed", response=response, body=None)
    if kind == "generic_server":
        return APIError(
            "An error occurred while processing your request. You can retry your request.",
            request,
            body=None,
        )
    return APIError("Provider failed", request, body={"code": kind})


@pytest.mark.parametrize(
    "kind", ["server_error", "generic_server", "500", "429", "connection", "timeout", "wrapped-503"]
)
def test_transient_provider_failure_recovers_without_changing_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: Policy,
    draft: WorkflowBuild,
    api: dict[str, Any],
    kind: str,
) -> None:
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    prompts: list[list[ModelMessage]] = []
    outputs = iter([policy.model_dump_json(), draft.model_dump_json()])

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        prompts.append(messages)
        if len(prompts) == 1:
            raise provider_failure(kind)
        return ModelResponse(parts=[TextPart(next(outputs))])

    @asynccontextmanager
    async def factory(_name: str, _seconds: float, _reasoning: str) -> AsyncGenerator[Model]:
        yield FunctionModel(respond)

    out = tmp_path / "recovered"
    inputs = Inputs(
        sources={"brief-1": "Check identity."}, api=api, references="fixture", manifest=[]
    )
    asyncio.run(Builder(inputs, out, attempts=2, model_factory=factory).run())
    assert len(prompts) == 3
    user_prompts = [
        [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        for messages in prompts[:2]
    ]
    assert user_prompts[0] == user_prompts[1]
    assert (out / "workflow.json").is_file()
    events = [json.loads(line) for line in (out / "trace.jsonl").read_text().splitlines()]
    assert len([event for event in events if event["event"] == "model_request_retry"]) == 1
    assert events[-1]["status"] == "locally_validated"


@pytest.mark.parametrize("second_policy_succeeds", [False, True])
def test_policy_retries_preserve_workflow_time_in_actual_request_path(
    tmp_path, monkeypatch, policy, draft, api, second_policy_succeeds
):
    import freya_hackathon.builder as builder_module

    clock = [0.0]
    timeouts = []
    outputs = iter([policy.model_dump_json(), draft.model_dump_json()])
    monkeypatch.setattr(builder_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)

    async def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(builder_module.asyncio, "sleep", sleep)

    def respond(_messages, _info):
        return ModelResponse(parts=[TextPart(next(outputs))])

    @asynccontextmanager
    async def factory(_name, seconds, _reasoning):
        timeouts.append(seconds)
        if len(timeouts) == 1 or not second_policy_succeeds:
            clock[0] += seconds
            raise TimeoutError("Synthetic provider timeout")
        clock[0] += 250 if len(timeouts) == 2 else 100
        yield FunctionModel(respond)

    out = tmp_path / "reserved"
    inputs = Inputs(
        sources={"brief-1": "Check identity."}, api=api, references="fixture", manifest=[]
    )
    builder = Builder(
        inputs, out, model_factory=factory, seconds=1200, call_seconds=600, attempts=2
    )
    if second_policy_succeeds:
        asyncio.run(builder.run())
        assert timeouts == [600, 298, 348]
        assert (out / "workflow.json").is_file()
        assert clock[0] == 952
    else:
        with pytest.raises(TimeoutError):
            asyncio.run(builder.run())
        assert timeouts == [600, 298]
        assert builder.deadline - clock[0] == 300
        assert not (out / "workflow.json").exists()


@pytest.mark.parametrize("kind", ["400", "401", "403", "invalid_api_key", "wrapped-401"])
def test_permanent_provider_failure_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    calls = 0
    failure = provider_failure(kind)

    def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        raise failure

    @asynccontextmanager
    async def factory(_name: str, _seconds: float, _reasoning: str) -> AsyncGenerator[Model]:
        yield FunctionModel(respond)

    out = tmp_path / "permanent-failure"
    inputs = Inputs(sources={"brief-1": "Check identity."}, api={}, references="", manifest=[])
    with pytest.raises(type(failure)) as raised:
        asyncio.run(Builder(inputs, out, attempts=3, model_factory=factory).run())
    assert raised.value is failure
    assert calls == 1
    assert not (out / "workflow.json").exists()


@pytest.mark.parametrize(("seconds", "expected_calls"), [(30, 2), (0.1, 1)])
def test_provider_retries_respect_attempt_and_total_time_budgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seconds: float, expected_calls: int
) -> None:
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    calls = 0
    failure = provider_failure("server_error")

    def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        raise failure

    @asynccontextmanager
    async def factory(_name: str, _seconds: float, _reasoning: str) -> AsyncGenerator[Model]:
        yield FunctionModel(respond)

    out = tmp_path / "exhausted"
    inputs = Inputs(sources={"brief-1": "Check identity."}, api={}, references="", manifest=[])
    with pytest.raises(type(failure)):
        asyncio.run(Builder(inputs, out, seconds=seconds, attempts=2, model_factory=factory).run())
    assert calls == expected_calls
    assert not (out / "workflow.json").exists()
    events = [json.loads(line) for line in (out / "trace.jsonl").read_text().splitlines()]
    assert events[-1]["status"] == "failed"


def test_timeout_leaves_failed_trace_and_no_final_workflow(tmp_path: Path) -> None:
    async def stalled(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(10)
        raise AssertionError("Timeout should cancel this request")

    @asynccontextmanager
    async def factory(_name: str, _seconds: float, _reasoning: str) -> AsyncGenerator[Model]:
        yield FunctionModel(stalled)

    out = tmp_path / "timed-out"
    inputs = Inputs(sources={"brief-1": "Check identity."}, api={}, references="", manifest=[])
    with pytest.raises(TimeoutError):
        asyncio.run(Builder(inputs, out, seconds=0.1, model_factory=factory).run())
    events = [json.loads(line) for line in (out / "trace.jsonl").read_text().splitlines()]
    assert events[-1]["status"] == "failed"
    assert not (out / "workflow.json").exists()


@pytest.mark.parametrize("problem", ["unresolved", "external_api"])
def test_failed_review_cannot_publish_a_final_workflow(
    tmp_path: Path, draft: WorkflowBuild, policy: Policy, api: dict[str, Any], problem: str
) -> None:
    if problem == "unresolved":
        review = WorkflowReview(unresolved=["Cannot implement the required identity gate"])
    else:
        replacement = draft.model_copy(deep=True)
        replacement.workflow["nodes"]["identity"]["tools"][0]["function"]["parameters"][
            "properties"
        ]["url"]["default"] = "https://outside.example/verify_identity"
        review = WorkflowReview(findings=["Change the identity tool"], replacement=replacement)
    outputs = iter([policy.model_dump_json(), draft.model_dump_json(), review.model_dump_json()])

    @asynccontextmanager
    async def factory(_name: str, _seconds: float, _reasoning: str) -> AsyncGenerator[Model]:
        yield FunctionModel(lambda _messages, _info: ModelResponse(parts=[TextPart(next(outputs))]))

    inputs = Inputs(sources={"brief-1": "Check identity."}, api=api, references="", manifest=[])
    out = tmp_path / problem
    with pytest.raises(ValueError, match="workflow-review failed local validation"):
        asyncio.run(Builder(inputs, out, model_factory=factory, attempts=1, review=True).run())
    assert (out / "before-review.json").exists()
    assert not (out / "workflow.json").exists()
    events = [json.loads(line) for line in (out / "trace.jsonl").read_text().splitlines()]
    assert events[-1]["status"] == "failed"
    assert any(e["event"] == "stage_rejected" and e["stage"] == "workflow-review" for e in events)


def test_review_requires_actionable_corrections_and_preserves_its_inputs(draft: WorkflowBuild):
    no_changes = WorkflowReview().apply(draft)
    assert no_changes == draft and no_changes is not draft
    replacement = draft.model_copy(deep=True)
    replacement.workflow["system_prompt"] += " Verify identity before disclosure."
    review = WorkflowReview(findings=["Missing disclosure guard"], replacement=replacement)
    result = review.apply(draft)
    assert result == replacement and result is not replacement
    assert result != draft
    edit = TextEdit(path=["workflow", "system_prompt"], before="missing", after="fixed")
    for invalid in (
        WorkflowReview(findings=["Defect without a repair"]),
        WorkflowReview(replacement=replacement),
        WorkflowReview(findings=["No real change"], replacement=draft),
        WorkflowReview(findings=["Ambiguous repair"], edits=[edit], replacement=replacement),
    ):
        with pytest.raises(ValueError):
            invalid.apply(draft)


def test_rejected_policy_never_reaches_workflow_generation(
    tmp_path: Path, policy: Policy, api: dict[str, Any]
) -> None:
    calls = 0

    def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart(policy.model_dump_json())])

    @asynccontextmanager
    async def factory(_name: str, _seconds: float, _reasoning: str) -> AsyncGenerator[Model]:
        yield FunctionModel(respond)

    out = tmp_path / "invalid-policy"
    inputs = Inputs(
        sources={"brief-1": "Different bank instructions."}, api=api, references="", manifest=[]
    )
    with pytest.raises(ValueError, match="does not occur"):
        asyncio.run(Builder(inputs, out, attempts=2, model_factory=factory).run())
    assert calls == 2
    assert not (out / "workflow.json").exists()


@pytest.mark.parametrize(
    ("text_repair", "with_reference"), [(False, False), (True, False), (True, True)]
)
def test_policy_reuse_checks_inputs_and_saves_provenance(
    tmp_path: Path,
    draft: WorkflowBuild,
    policy: Policy,
    api: dict[str, Any],
    text_repair: bool,
    with_reference: bool,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    manifest = [{"source": "brief-1", "sha256": "a" * 64, "path": "/original/brief.md"}]
    if with_reference:
        manifest.append(
            {"source": "development-reference-1", "sha256": "c" * 64, "path": "/reference.json"}
        )
    (source / "inputs.json").write_text(json.dumps(manifest))
    (source / "policy.json").write_text(policy.model_dump_json())
    (source / "trace.jsonl").write_text('{"event":"builder_end","elapsed_seconds":100}\n')
    (source / "model-01.messages.json").write_text("[]")
    (source / "workflow.json").write_text(json.dumps(draft.workflow))
    (source / "rule-nodes.json").write_text(json.dumps(draft.rule_nodes))
    ancestor = source / "provenance" / "ancestor"
    ancestor.mkdir(parents=True)
    (ancestor / "evidence.txt").write_text("original generation evidence")
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("Preserve explicit refusal beside a question.")
    calls = 0

    def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        response = draft.model_dump_json()
        if text_repair:
            response = WorkflowTextRepair(
                edits=[
                    TextEdit(
                        path=["workflow", "nodes", "identity", "task_message"],
                        before=draft.workflow["nodes"]["identity"]["task_message"],
                        after=draft.workflow["nodes"]["identity"]["task_message"]
                        + " Preserve privacy.",
                    )
                ]
            ).model_dump_json()
        return ModelResponse(parts=[TextPart(response)])

    @asynccontextmanager
    async def factory(_name: str, _seconds: float, _reasoning: str) -> AsyncGenerator[Model]:
        yield FunctionModel(respond)

    inputs = Inputs(
        sources={"brief-1": "Check identity."},
        api=api,
        references="fixture",
        manifest=manifest,
        workflow_references={"development-reference-1": draft.workflow} if with_reference else {},
    )
    out = tmp_path / "reuse"
    asyncio.run(
        Builder(
            inputs,
            out,
            model_factory=factory,
            reuse_policy_from=source,
            repair_from=source,
            feedback=feedback,
            text_repair=text_repair,
        ).run()
    )
    assert calls == 1
    if text_repair:
        changed = json.loads((out / "workflow.json").read_text())
        assert changed["nodes"]["identity"]["task_message"].endswith(" Preserve privacy.")
    assert len(list((out / "provenance").glob("*/policy.json"))) == 1
    assert len(list((out / "provenance").glob("*/trace.jsonl"))) == 1
    assert len(list((out / "provenance").glob("*/model-01.messages.json"))) == 1
    assert len(list((out / "provenance").rglob("evidence.txt"))) == 1
    assert (out / "repair-feedback.txt").read_text() == feedback.read_text()
    assert json.loads((out / "execution-scope.json").read_text())["fresh_submission_run"] is False
    assert (
        json.loads((out / "execution-scope.json").read_text())["uses_public_development_references"]
        is with_reference
    )

    inputs.manifest[0]["sha256"] = "b" * 64
    repair_options: dict[str, Any] = (
        {"repair_from": source, "feedback": feedback, "text_repair": True} if with_reference else {}
    )
    with pytest.raises(ValueError, match="input documents or kit references changed"):
        asyncio.run(
            Builder(
                inputs,
                tmp_path / "wrong",
                model_factory=factory,
                reuse_policy_from=source,
                **repair_options,
            ).run()
        )
    assert calls == 1


@pytest.mark.parametrize("mode", ["policy_only", "whole_workflow"])
def test_reference_reuse_requires_explicit_text_repair(tmp_path: Path, mode: str) -> None:
    inputs = Inputs(
        sources={}, api={}, references="", manifest=[], workflow_references={"reference": {}}
    )
    options: dict[str, Any] = (
        {"reuse_policy_from": tmp_path}
        if mode == "policy_only"
        else {"repair_from": tmp_path, "feedback": tmp_path / "feedback.txt"}
    )
    with pytest.raises(ValueError, match="cannot be combined"):
        Builder(inputs, tmp_path / "out", **options)


def test_repair_requires_explicit_feedback(tmp_path: Path) -> None:
    inputs = Inputs(sources={}, api={}, references="", manifest=[])
    with pytest.raises(ValueError, match="both a prior run and a feedback"):
        Builder(inputs, tmp_path / "out", repair_from=tmp_path)


def test_text_repairs_are_exact_and_do_not_mutate_source() -> None:
    original = WorkflowBuild(workflow={"nodes": [{"code": "one\ntwo"}]}, rule_nodes={})
    edit = TextEdit(path=["workflow", "nodes", 0, "code"], before="two", after="three")
    repaired = WorkflowTextRepair(edits=[edit]).apply(original)
    assert repaired.workflow["nodes"][0]["code"] == "one\nthree"
    assert original.workflow["nodes"][0]["code"] == "one\ntwo"
    for bad in (
        edit.model_copy(update={"before": "missing"}),
        edit.model_copy(update={"before": "e"}),
        edit.model_copy(update={"path": ["workflow", "nodes", -1, "code"]}),
    ):
        with pytest.raises(ValueError):
            WorkflowTextRepair(edits=[edit, bad]).apply(original)
        assert original.workflow["nodes"][0]["code"] == "one\ntwo"


def test_optional_noop_still_validates_target_and_keeps_repairs_strict() -> None:
    original = WorkflowBuild(workflow={"nodes": [{"code": "one\ntwo"}]}, rule_nodes={})
    noop = TextEdit(path=["workflow", "nodes", 0, "code"], before="two", after="two")
    change = noop.model_copy(update={"after": "three"})
    edits = WorkflowTextRepair(edits=[noop, change])
    with pytest.raises(ValueError, match="must change"):
        edits.apply(original)
    assert edits.apply(original, allow_noop=True).workflow["nodes"][0]["code"] == "one\nthree"
    for bad in (
        noop.model_copy(update={"before": "missing", "after": "missing"}),
        noop.model_copy(update={"before": "o", "after": "o"}),
        noop.model_copy(update={"path": ["workflow", "missing"]}),
    ):
        with pytest.raises(ValueError):
            WorkflowTextRepair(edits=[bad]).apply(original, allow_noop=True)
    assert original.workflow["nodes"][0]["code"] == "one\ntwo"
