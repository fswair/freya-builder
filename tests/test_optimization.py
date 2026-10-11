from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai import models

from freya_hackathon.builder import write_json
from freya_hackathon.optimization import (
    Batch,
    Study,
    evaluate_holdout,
    repeated_score,
    validate_guidance,
)
from freya_hackathon.optimization_data import StudyConfig, load_development
from freya_hackathon.optimization_smoke import (
    FakePractice,
    fake_builder,
    fake_reflection_model,
    prepare,
    run,
)
from freya_hackathon.practice import WorkflowRejected


def test_real_gepa_builder_record_replays_without_network(tmp_path: Path) -> None:
    report = run(tmp_path / "smoke")
    assert report["builds"] == 2  # train and validation share each candidate's generated workflow
    assert report["offline_replay"] == "passed"
    assert report["holdout_opened"] is False
    assert "pydantic_gepa.optimization" in report["native_operations"]
    assert "pydantic_ai.model.request" in report["native_operations"]
    assert "httpx.request" in report["native_operations"]


@pytest.mark.parametrize("all_perfect", [False, True])
def test_bounded_reflection_includes_both_briefs_even_when_perfect(
    tmp_path: Path, all_perfect: bool
) -> None:
    config = prepare(tmp_path / "fixture")
    splits = load_development(config)
    config.briefs["B"] = config.briefs["A"].model_copy(deep=True)
    config.metric_calls, config.max_builds, config.max_reflections = 8, 4, 1
    for rows in splits.values():
        rows.append({**rows[0], "id": rows[0]["id"] + "-B", "brief": "B"})

    class PerfectBriefPractice(FakePractice):
        def execute(self, cases: Path, _brief: str, _slot: str):
            report = super().execute(cases, _brief, _slot)
            if all_perfect or _brief == "B":
                report["passed"] = report["total"]
                for row in report["rows"]:
                    row.update({"pass": True, "failed": []})
                write_json(self.run / "practice/results.json", report)
            return report

    output = tmp_path / "study"
    output.mkdir()
    study = Study(
        config,
        output,
        splits,
        builder_factory=fake_builder,
        practice_factory=PerfectBriefPractice,
        reflection_factory=fake_reflection_model,
        evidence_scope="offline-integration",
    )
    seen = []

    def propose(candidate, evidence, components):
        seen.append(evidence)
        return study.propose(candidate, evidence, components)

    with models.override_allow_model_requests(False):
        selection = study.optimize(propose)

    assert study.reflection_count == len(seen) == 1
    assert len(seen[0]["guidance"]) == 2
    evidence_text = json.dumps(seen[0])
    assert "train-A" in evidence_text and "train-B" in evidence_text
    assert selection["builds"] == 4
    assert selection["metric_calls"] == (6 if all_perfect else 8)
    assert selection["validation_score"] == 1.0
    evaluations = [json.loads(p.read_text()) for p in output.glob("evaluations/*/evaluation.json")]
    validation = [item for item in evaluations if "-validation-" in item["path"]]
    assert len(validation) == (2 if all_perfect else 4)
    assert all(item["repetitions"] == 3 for item in validation)


def test_study_resolves_and_fingerprints_development_references(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    prepare(fixture)
    reference = fixture / "reference.json"
    write_json(reference, {"nodes": {"start": {"name": "original reference"}}})
    config_path = fixture / "study.json"
    raw = json.loads(config_path.read_text())
    raw["workflow_references"] = ["reference.json"]
    write_json(config_path, raw)
    config = StudyConfig.read(config_path)
    assert config.workflow_references == [reference.resolve()]
    original = config.inputs()["A"]
    assert "development-reference-1" in original.workflow_references
    write_json(reference, {"nodes": {"start": {"name": "changed reference"}}})
    changed = config.inputs()["A"]
    assert original.manifest != changed.manifest
    assert original.sources == changed.sources


def test_real_gepa_does_not_build_next_brief_after_unclean_evidence(tmp_path: Path) -> None:
    config = prepare(tmp_path / "fixture")
    splits = load_development(config)
    config.briefs["B"] = config.briefs["A"].model_copy(deep=True)
    for rows in splits.values():
        rows.append({**rows[0], "id": rows[0]["id"] + "-B", "brief": "B"})
    calls = []

    class IncompletePractice(FakePractice):
        def execute(self, cases: Path, _brief: str, _slot: str):
            calls.append(_brief)
            report = super().execute(cases, _brief, _slot)
            path = self.run / "practice/runtime-health.json"
            health = json.loads(path.read_text())
            health["incomplete_evidence_cases"] = [report["rows"][0]["id"]]
            health["usable_as_clean_model_comparison"] = False
            write_json(path, health)
            return report

    output = tmp_path / "study"
    output.mkdir()
    study = Study(
        config,
        output,
        splits,
        builder_factory=fake_builder,
        practice_factory=IncompletePractice,
        reflection_factory=fake_reflection_model,
        evidence_scope="offline-integration",
    )
    with models.override_allow_model_requests(False):
        with pytest.raises(RuntimeError, match="Optimization aborted"):
            study.optimize()
    assert study.build_count == 1
    assert calls == ["A"]
    assert study.reflection_count == 0
    assert not (output / "selection.json").exists()


@pytest.mark.parametrize("violation", ["group", "script", "id", "fixture"])
def test_ingestion_rejects_leakage_and_modified_bank_facts(tmp_path: Path, violation: str) -> None:
    config = prepare(tmp_path / "fixture")
    split = load_development(config)  # intentionally invalid holdout must remain unopened
    train, validation = split["train"][0], split["validation"][0]
    if violation == "group":
        validation["metadata"]["group_id"] = train["metadata"]["group_id"]
    elif violation == "script":
        validation["script"] = "  " + train["script"].upper() + " "
    elif violation == "id":
        validation["id"] = train["id"]
    else:
        validation["variables"]["now"] = "2099-01-01T11:00"
    write_json(config.dataset / "validation.json", [validation])
    with pytest.raises(ValueError, match="leakage|fixture"):
        load_development(config)


def test_case_must_pass_all_repetitions() -> None:
    def report(a: bool, b: bool):
        return {
            "status": "completed",
            "total": 2,
            "rows": [{"id": "a", "pass": a}, {"id": "b", "pass": b}],
        }

    score = repeated_score(
        [report(True, True), report(True, False), report(True, True)], ["a", "b"]
    )
    assert score["score"] == 0.5
    assert score["passed"] == 1
    with pytest.raises(RuntimeError, match="Incomplete"):
        repeated_score([report(True, True)], ["a", "different"])


@pytest.mark.parametrize("text", ["", "Remember PUB-06", "Offer 2026-10-16", "x" * 12_001])
def test_guidance_rejects_case_specific_literals_and_bad_sizes(text: str) -> None:
    with pytest.raises(ValueError):
        validate_guidance(text)


@pytest.mark.parametrize(
    ("field", "value", "remove"),
    [
        (field, value, remove)
        for field in (
            "infrastructure_affected_cases",
            "unresolved_runtime_error_cases",
            "simulator_error_cases",
            "incomplete_evidence_cases",
        )
        for value, remove in ((["DEV-train"], False), (None, True), ([1], False))
    ]
    + [("usable_as_clean_model_comparison", value, False) for value in (False, None, "true", 1)]
    + [("usable_as_clean_model_comparison", None, True)],
)
def test_unclean_or_missing_health_aborts_instead_of_scoring_model_failure(
    tmp_path: Path, field: str, value, remove: bool
) -> None:
    config = prepare(tmp_path / "fixture")
    splits = load_development(config)

    class BrokenPractice(FakePractice):
        def execute(self, cases: Path, _brief: str, _slot: str):
            report = super().execute(cases, _brief, _slot)
            path = self.run / "practice/runtime-health.json"
            health = json.loads(path.read_text())
            if remove:
                del health[field]
            else:
                health[field] = value
            write_json(path, health)
            return report

    out = tmp_path / "study"
    out.mkdir()
    study = Study(
        config, out, splits, builder_factory=fake_builder, practice_factory=BrokenPractice
    )
    with study.context.use("Use compact steps."):
        with pytest.raises(RuntimeError, match="abort selection"):
            study.evaluate(Batch(brief="A", split="train", cases=splits["train"]))
    assert study.aborted == "RuntimeError"
    assert not (out / "selection.json").exists()
    assert not list((out / "evaluations").glob("*/evaluation.json"))


def test_offline_selection_cannot_launch_live_holdout(tmp_path: Path) -> None:
    selected = tmp_path / "selection.json"
    selected.write_text(json.dumps({"evidence_scope": "offline-integration"}))
    with pytest.raises(ValueError, match="real practice"):
        evaluate_holdout(selected, tmp_path / "out")


def test_workflow_rejection_is_candidate_feedback(tmp_path: Path) -> None:
    config = prepare(tmp_path / "fixture")
    splits = load_development(config)

    class RejectedPractice(FakePractice):
        def execute(self, cases: Path, _brief: str, _slot: str):
            directory = self.run / "practice"
            directory.mkdir(exist_ok=True)
            write_json(directory / "freya-validation.json", {"valid": False, "error": "bad edge"})
            raise WorkflowRejected("Workflow failed Freya dry-run validation")

    out = tmp_path / "study"
    out.mkdir()
    study = Study(
        config, out, splits, builder_factory=fake_builder, practice_factory=RejectedPractice
    )
    with study.context.use("Use compact steps."):
        report = study.evaluate(Batch(brief="A", split="train", cases=splits["train"]))
    assert report["score"] == 0
    assert "bad edge" in report["detail"]
    assert study.aborted is None


def test_holdout_reuses_frozen_workflow_and_rejects_changes(tmp_path: Path) -> None:
    # Change only the fixture's evidence label so the live-only command can be
    # exercised with an explicitly injected HTTPX mock. No external request occurs.
    run(tmp_path / "smoke")
    selected_path = tmp_path / "smoke/study/selection.json"
    selected = json.loads(selected_path.read_text())
    selected["evidence_scope"] = "practice"
    write_json(selected_path, selected)
    dataset = Path(selected["config"]["dataset"])
    case = json.loads((dataset / "train.json").read_text())[0]
    case.update(
        id="DEV-HOLDOUT",
        script="A new held-out integration probe.",
        metadata={"split": "holdout", "group_id": "unseen"},
    )
    write_json(dataset / "holdout.json", [case])
    result = evaluate_holdout(selected_path, tmp_path / "holdout", practice_factory=FakePractice)
    assert result["optimization_performed"] is False
    assert result["reports"][0]["repetitions"] == 3
    assert result["reports"][0]["score"] == 1.0
    assert not (tmp_path / "holdout/candidates").exists()
    assert (tmp_path / "holdout/selected-guidance.txt").read_text() == (
        selected_path.parent / "selected-guidance.txt"
    ).read_text()

    selected["selected_builds"].clear()
    write_json(selected_path, selected)
    with pytest.raises(ValueError, match="frozen workflow"):
        evaluate_holdout(selected_path, tmp_path / "missing", practice_factory=FakePractice)
    selected = json.loads((tmp_path / "holdout/frozen-selection.json").read_text())
    write_json(selected_path, selected)

    built = selected_path.parent / selected["selected_builds"]["A"]["path"] / "workflow.json"
    built.write_text("{}")
    with pytest.raises(ValueError, match="workflow changed"):
        evaluate_holdout(selected_path, tmp_path / "tampered", practice_factory=FakePractice)
