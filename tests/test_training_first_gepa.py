"""Search learns from training only; repeated validation alone chooses a candidate."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
from pathlib import Path

import httpx
import pytest
from pydantic_ai import models

from freya_hackathon.builder import write_json
from freya_hackathon.optimization import source_fingerprint
from freya_hackathon.optimization_data import load_development
from freya_hackathon.optimization_smoke import (
    FakePractice,
    fake_builder,
    fake_reflection_model,
    prepare,
)
from freya_hackathon.recording import capture_sync

SPEC = importlib.util.spec_from_file_location(
    "training_first_gepa",
    Path(__file__).resolve().parents[1] / "diagnostics/training_first_gepa.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize(
    ("method", "status", "expected_calls"),
    [("GET", None, 3), ("POST", None, 1), ("GET", 401, 1), ("GET", 503, 3)],
)
def test_transient_reads_retry_but_writes_and_auth_failures_do_not(
    monkeypatch, method, status, expected_calls
):
    calls = []

    def api(_self, method, path, body=None, **params):
        calls.append(method)
        if len(calls) < 3:
            if status is None:
                raise httpx.ReadTimeout("synthetic timeout")
            response = httpx.Response(status, request=httpx.Request(method, "https://test.invalid"))
            response.raise_for_status()
        return {"success": True}

    monkeypatch.setattr(MODULE.Practice, "api", api)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)
    practice = object.__new__(MODULE.ReadResilientPractice)
    practice.remaining = lambda: 45
    practice.trace = lambda *args, **kwargs: None
    if expected_calls == 1:
        with pytest.raises((httpx.ReadTimeout, httpx.HTTPStatusError)):
            practice.api(method, "/synthetic")
    else:
        assert practice.api(method, "/synthetic") == {"success": True}
    assert len(calls) == expected_calls


def test_read_retry_budget_is_finite(monkeypatch):
    calls = []

    def api(*args, **kwargs):
        calls.append(True)
        raise httpx.ReadTimeout("synthetic timeout")

    monkeypatch.setattr(MODULE.Practice, "api", api)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)
    practice = object.__new__(MODULE.ReadResilientPractice)
    practice.remaining = lambda: 45
    practice.trace = lambda *args, **kwargs: None
    with pytest.raises(httpx.ReadTimeout):
        practice.api("GET", "/synthetic")
    assert len(calls) == 3


def test_failure_feedback_preserves_trigger_and_final_turns_without_system_payloads():
    messages = [{"role": "system", "message": "PRIVATE SYSTEM PAYLOAD"}]
    messages += [
        {"role": "user" if i % 2 else "assistant", "message": f"turn {i}: " + "x" * 500}
        for i in range(40)
    ]
    row = {"id": "DEV-TR-B-007", "simulation_reason": "r" * 2000}
    result = MODULE.summarize_training_failure(row, messages)
    assert [m["turn"] for m in result["dialogue"]] == list(range(10)) + list(range(30, 40))
    assert result["dialogue_turns"] == 40
    assert result["omitted_turns"] == 20
    assert all(len(m["text"]) <= 240 for m in result["dialogue"])
    assert len(result["judge_reason"]) == 1000
    assert "PRIVATE SYSTEM PAYLOAD" not in json.dumps(result)


def test_short_training_feedback_does_not_duplicate_turns():
    messages = [{"role": "user", "message": str(i)} for i in range(13)]
    result = MODULE.summarize_training_failure({"id": "DEV-TR-A-001"}, messages)
    assert [m["turn"] for m in result["dialogue"]] == list(range(13))
    assert result["omitted_turns"] == 0


def test_local_quality_failure_reaches_reflection_without_deploying_failed_build(tmp_path):
    config = prepare(tmp_path / "fixture")
    config.metric_calls, config.max_builds, config.max_reflections = 8, 4, 1
    config.validation_repetitions = 3
    splits = load_development(config)
    calls = []

    def gate(build):
        workflow = json.loads((build / "workflow.json").read_text())
        passed = workflow["nodes"]["identity"]["task_message"] == "Request consent."
        return {"pass": passed, "assertion_failures": [] if passed else ["Missing consent guard"]}

    class CheckedPractice(FakePractice):
        def execute(self, cases, _brief, _slot):
            workflow = json.loads((self.run / "workflow.json").read_text())
            assert workflow["nodes"]["identity"]["task_message"] == "Request consent."
            calls.append(self.run)
            return super().execute(cases, _brief, _slot)

    output = tmp_path / "study"
    output.mkdir()
    study = MODULE.TrainingFirstStudy(
        config,
        output,
        splits,
        builder_factory=fake_builder,
        practice_factory=CheckedPractice,
        reflection_factory=fake_reflection_model,
        evidence_scope="offline-integration",
        local_gate=gate,
    )
    reflected = []

    def proposer(candidate, evidence, components):
        reflected.append(json.dumps(evidence))
        return study.propose(candidate, evidence, components)

    with models.override_allow_model_requests(False):
        selection = study.optimize(proposer)
    assert len(calls) == 4  # One child training simulation and three validation repeats.
    assert "Missing consent guard" in reflected[0]
    assert selection["selected_index"] == 1
    assert selection["validation_score"] == 1.0
    assert selection["local_behavior_gates"] is True


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        ([{"A": 0.5, "B": 0.5}, {"A": 0.5, "B": 0.5}], 0),
        ([{"A": 0.5, "B": 0.5}, {"A": 1.0, "B": 0.5}], 1),
        ([{"A": 0.0, "B": 1.0}, {"A": 1.0, "B": 0.75}], 0),
    ],
)
def test_selection_requires_gain_without_a_brief_regression(scores, expected):
    assert MODULE.choose_validation_candidate(scores) == expected


@pytest.mark.parametrize(
    "scores", [[], [{}], [{"A": 1}, {"B": 1}], [{"A": float("nan")}], [{"A": True}]]
)
def test_selection_rejects_incomplete_or_invalid_scores(scores):
    with pytest.raises(ValueError):
        MODULE.choose_validation_candidate(scores)


def test_actual_gepa_search_finishes_before_validation_and_cannot_select_by_train_score(tmp_path):
    config = prepare(tmp_path / "fixture")
    splits = load_development(config)
    config.briefs["B"] = config.briefs["A"].model_copy(deep=True)
    config.metric_calls, config.max_builds, config.max_reflections = 8, 4, 1
    for rows in splits.values():
        rows.append({**rows[0], "id": rows[0]["id"] + "-B", "brief": "B"})
    calls = []

    class RegressingValidation(FakePractice):
        def execute(self, cases, _brief, _slot):
            brief, slot = _brief, _slot
            split = json.loads(cases.read_text())[0]["metadata"]["split"]
            calls.append((split, brief))
            result = super().execute(cases, brief, slot)
            if split == "validation":
                # The training winner regresses on B. Initial validation is perfect.
                improved = result["passed"] == result["total"]
                passed = not improved or brief == "A"
                result["passed"] = result["total"] if passed else 0
                for row in result["rows"]:
                    row.update({"pass": passed, "failed": [] if passed else ["regression"]})
                write_json(self.run / "practice/results.json", result)
            return result

    output = tmp_path / "study"
    output.mkdir()
    study = MODULE.TrainingFirstStudy(
        config,
        output,
        splits,
        builder_factory=fake_builder,
        practice_factory=RegressingValidation,
        reflection_factory=fake_reflection_model,
        evidence_scope="offline-integration",
    )
    reflections = []

    def propose(candidate, evidence, components):
        assert all(split == "train" for split, _ in calls)
        assert "DEV-validation" not in json.dumps(evidence)
        reflections.append(evidence)
        return study.propose(candidate, evidence, components)

    with models.override_allow_model_requests(False):
        selection = study.optimize(propose)
    assert len(reflections) == 1
    assert selection["training_gepa_best_score"] == 1.0
    assert selection["selected_index"] == 0
    assert selection["validation_by_brief"] == {"A": 1.0, "B": 1.0}
    assert selection["training_cache_hits"] >= 2
    assert len([c for c in calls if c[0] == "train"]) == 4
    assert len([c for c in calls if c[0] == "validation"]) == 12
    assert all(split == "train" for split, _ in calls[:4])
    assert all(split == "validation" for split, _ in calls[4:])
    assert selection["builds"] == 4
    assert selection["holdout_evaluated"] is False


def test_incomplete_postsearch_validation_cannot_write_selection(tmp_path):
    config = prepare(tmp_path / "fixture")
    config.max_reflections = 1
    output = tmp_path / "study"
    output.mkdir()

    class MissingEvidence(FakePractice):
        def execute(self, cases, _brief, _slot):
            brief, slot = _brief, _slot
            result = super().execute(cases, brief, slot)
            if "validation" in cases.parent.name:
                path = self.run / "practice/runtime-health.json"
                health = json.loads(path.read_text())
                health.update(
                    incomplete_evidence_cases=["missing"], usable_as_clean_model_comparison=False
                )
                write_json(path, health)
            return result

    study = MODULE.TrainingFirstStudy(
        config,
        output,
        load_development(config),
        builder_factory=fake_builder,
        practice_factory=MissingEvidence,
        reflection_factory=fake_reflection_model,
        evidence_scope="offline-integration",
    )
    with models.override_allow_model_requests(False), pytest.raises(RuntimeError):
        study.optimize()
    assert study.phase == "validation"
    assert (output / "training-gepa-result.json").exists()
    assert not (output / "selection.json").exists()
    assert not (output / "selected-guidance.txt").exists()


@pytest.fixture
def seed_fixture(tmp_path):
    config = prepare(tmp_path / "fixture")
    source = tmp_path / "seed"
    source.mkdir()
    parent = source / "A"
    with models.override_allow_model_requests(False):
        asyncio.run(
            fake_builder(
                config.inputs()["A"],
                parent,
                model=config.model,
                reasoning=config.reasoning,
                guidance=config.guidance.read_text(),
            ).run()
        )
    write_json(source / "experiment.json", {"source_fingerprint": source_fingerprint()})
    output = tmp_path / "study"
    output.mkdir()
    study = MODULE.TrainingFirstStudy(config, output, load_development(config))
    return study, source, parent


def test_seed_reuses_exact_native_captured_build_and_counts_it_in_budget(seed_fixture):
    study, source, parent = seed_fixture
    MODULE.seed_builds(study, source)
    assert study.build_count == 1
    build = study.build("A", study.config.guidance.read_text())
    assert study.build_count == 1
    assert (build / "workflow.json").read_bytes() == (parent / "workflow.json").read_bytes()
    provenance = json.loads((study.output / "seed-builds.json").read_text())
    assert provenance[0]["files"]["workflow.json"]


def test_partial_seed_does_not_require_or_substitute_the_other_brief(seed_fixture):
    study, source, _ = seed_fixture
    study.config.briefs["B"] = study.config.briefs["A"].model_copy(deep=True)
    MODULE.seed_builds(study, source, briefs={"A"})
    assert study.build_count == 1
    assert {brief for brief, _ in study.builds} == {"A"}


def test_seed_uses_the_same_canonical_guidance_as_optimizer(seed_fixture):
    study, source, parent = seed_fixture
    original = study.config.guidance.read_text()
    study.config.guidance.write_text("\n" + original + "\n")
    MODULE.seed_builds(study, source)
    canonical = MODULE.validate_guidance(study.config.guidance.read_text())
    build = study.build("A", canonical)
    assert study.build_count == 1
    assert (build / "workflow.json").read_bytes() == (parent / "workflow.json").read_bytes()


@pytest.fixture
def recovered_fixture(seed_fixture, tmp_path):
    study, source, parent = seed_fixture
    MODULE.seed_builds(study, source)

    def create(damage=None):
        recovery = tmp_path / "recovery"
        recovery.mkdir()
        cases = json.loads(json.dumps(study.splits["train"]))
        if damage == "cases":
            cases[0]["id"] = "different-case"

        def capture():
            (recovery / "practice").mkdir()
            shutil.copyfile(parent / "workflow.json", recovery / "workflow.json")
            if damage == "workflow":
                workflow = json.loads((recovery / "workflow.json").read_text())
                workflow["nodes"]["identity"]["task_message"] = "Different generated behavior"
                write_json(recovery / "workflow.json", workflow)
            guidance = MODULE.validate_guidance(study.config.guidance.read_text())
            write_json(
                recovery / "parent.json",
                {
                    "guidance_sha256": "different"
                    if damage == "guidance"
                    else MODULE.fingerprint(guidance)
                },
            )
            write_json(recovery / "practice/cases.json", cases)
            report = {
                "status": "completed",
                "total": len(cases),
                "passed": len(cases),
                "rows": [{"id": row["id"], "pass": True, "failed": []} for row in cases],
            }
            write_json(recovery / "practice/results.json", report)
            write_json(
                recovery / "practice/runtime-health.json",
                {
                    "usable_as_clean_model_comparison": damage != "health",
                    "infrastructure_affected_cases": [],
                    "unresolved_runtime_error_cases": [cases[0]["id"]]
                    if damage == "health"
                    else [],
                    "simulator_error_cases": [],
                    "incomplete_evidence_cases": [],
                },
            )
            return report

        capture_sync(recovery, "existing_simulation_recovery", capture)
        if damage == "immutable":
            path = recovery / "workflow.json"
            path.write_text(path.read_text() + "\n")
        return recovery

    return study, create


def test_clean_exact_recovered_training_is_reused_without_new_calls(recovered_fixture):
    study, create = recovered_fixture
    recovery = create()
    study.builder_factory = lambda *args, **kwargs: pytest.fail("Unexpected new model call")
    study.practice_factory = lambda *args, **kwargs: pytest.fail("Unexpected new simulation")
    MODULE.seed_recovered_training(study, recovery)
    guidance = MODULE.validate_guidance(study.config.guidance.read_text())
    with study.context.use(guidance):
        result = study.evaluate(study.examples("train")[0].inputs)
    assert result["score"] == 1.0
    assert result["reused_training"] is True
    assert study.cache_hits == 1
    assert study.build_count == 1


@pytest.mark.parametrize("damage", ["cases", "workflow", "guidance", "health", "immutable"])
def test_recovered_training_rejects_mismatched_or_unreliable_evidence(recovered_fixture, damage):
    study, create = recovered_fixture
    with pytest.raises(ValueError):
        MODULE.seed_recovered_training(study, create(damage))
    assert not study.training_cache


@pytest.mark.parametrize(
    "damage", ["guidance", "record_newline", "workflow", "model", "source", "incomplete"]
)
def test_seed_rejects_changed_or_incomplete_generation_before_copying(seed_fixture, damage):
    study, source, parent = seed_fixture
    if damage == "guidance":
        (parent / "guidance.txt").write_text("Changed guidance")
    elif damage == "record_newline":
        path = parent / "guidance.txt"
        path.write_text(path.read_text() + "\n")
    elif damage == "workflow":
        workflow = json.loads((parent / "workflow.json").read_text())
        workflow["nodes"]["identity"]["task_message"] = "Changed after generation"
        write_json(parent / "workflow.json", workflow)
    elif damage == "source":
        write_json(source / "experiment.json", {"source_fingerprint": "different"})
    else:
        trace = parent / "trace.jsonl"
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        if damage == "model":
            next(event for event in events if event["event"] == "builder_start")["model"] = "other"
        else:
            events = [event for event in events if event["event"] != "builder_end"]
        trace.write_text("".join(json.dumps(event) + "\n" for event in events))
    with pytest.raises(ValueError):
        MODULE.seed_builds(study, source)
    assert study.build_count == 0
    assert not (study.output / "candidates").exists()
