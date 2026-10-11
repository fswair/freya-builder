"""Effort comparisons fail closed when frozen inputs or baseline evidence differ."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "run_a_transfer", Path(__file__).resolve().parents[1] / "diagnostics/run_a_transfer.py"
)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


@pytest.fixture
def baseline(tmp_path: Path) -> tuple[Path, dict, list[dict]]:
    experiment = {
        "source_fingerprint": "source",
        "guidance_sha256": "guidance",
        "model": "same-model",
        "reasoning": "high",
        "public_ids": ["PUB-01"],
        "training_ids": ["TRAIN-01"],
        "generation_seconds": 1200,
        "total_seconds": 1800,
        "references": [{"path": "reference", "sha256": "hash"}],
        "holdout_opened": False,
    }
    cases = [{"id": "PUB-01", "customer": "original"}]
    (tmp_path / "A/practice").mkdir(parents=True)
    (tmp_path / "experiment.json").write_text(json.dumps(experiment))
    (tmp_path / "cases.json").write_text(json.dumps(cases))
    (tmp_path / "A/practice/runtime-health.json").write_text(
        json.dumps({"usable_as_clean_model_comparison": True})
    )
    return tmp_path, experiment, cases


def test_effort_is_the_allowed_change(baseline: tuple[Path, dict, list[dict]]) -> None:
    path, experiment, cases = baseline
    RUNNER.check_baseline(path, {**experiment, "reasoning": "xhigh"}, cases)


@pytest.mark.parametrize(
    "field", ["source_fingerprint", "guidance_sha256", "model", "total_seconds", "references"]
)
def test_confounded_input_is_rejected(baseline: tuple[Path, dict, list[dict]], field: str) -> None:
    path, experiment, cases = baseline
    with pytest.raises(ValueError, match="Frozen comparison inputs changed"):
        RUNNER.check_baseline(path, {**experiment, field: "changed"}, cases)


def test_same_case_id_with_changed_content_is_rejected(
    baseline: tuple[Path, dict, list[dict]],
) -> None:
    path, experiment, cases = baseline
    with pytest.raises(ValueError, match="case contents"):
        RUNNER.check_baseline(path, experiment, [{**cases[0], "customer": "changed"}])


def test_infrastructure_affected_baseline_is_rejected(
    baseline: tuple[Path, dict, list[dict]],
) -> None:
    path, experiment, cases = baseline
    (path / "A/practice/runtime-health.json").write_text(
        json.dumps({"usable_as_clean_model_comparison": False})
    )
    with pytest.raises(ValueError, match="clean evaluation evidence"):
        RUNNER.check_baseline(path, experiment, cases)
