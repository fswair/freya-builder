"""A lost transcript must never attach a different persona's bank ledger."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def evidence(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "diagnostics"))
    module = importlib.import_module("recover_seed_ledgers")
    state = {"id": "simulation-1", "status": "completed"}
    iteration = {
        "iteration": {"id": "iteration-1", "simulationId": "simulation-1"},
        "runs": [
            {
                "id": "run-1",
                "personaName": "CASE-1 Synthetic scenario",
                "personaId": "persona-1",
                "callId": None,
                "outcome": "error",
            }
        ],
    }
    seeds = {"persona-1": {"customer_id": "c_synthetic-1"}}
    result = {
        "simulation_id": "simulation-1",
        "iteration_id": "iteration-1",
        "rows": [
            {
                "id": "CASE-1",
                "simulation_run_id": "run-1",
                "call_id": None,
                "simulation_outcome": "error",
                "pass": False,
                "failed": ["Missing practice customer ID"],
            }
        ],
    }
    return module, state, iteration, seeds, result


def select(evidence):
    module, state, iteration, seeds, result = evidence
    return module.select_missing_ledgers("simulation-1", state, iteration, seeds, result)


def test_missing_call_uses_recorded_seed_but_preserves_failure(evidence):
    rows = select(evidence)
    assert rows[0]["customer_id"] == "c_synthetic-1"
    assert rows[0]["customer_id_source"] == "recorded_simulation_inputs"
    assert rows[0]["official_outcome"] == "error"
    assert rows[0]["original_combined_pass"] is False
    assert evidence[-1]["rows"][0]["failed"] == ["Missing practice customer ID"]


@pytest.mark.parametrize("status", ["running", "paused", "pending"])
def test_incomplete_simulation_is_rejected(evidence, status):
    evidence[1]["status"] = status
    with pytest.raises(ValueError, match="terminal"):
        select(evidence)


@pytest.mark.parametrize("location", ["state", "iteration", "result", "result_iteration"])
def test_cross_simulation_or_iteration_join_is_rejected(evidence, location):
    _, state, iteration, _, result = evidence
    if location == "state":
        state["id"] = "another-simulation"
    elif location == "iteration":
        iteration["iteration"]["simulationId"] = "another-simulation"
    elif location == "result":
        result["simulation_id"] = "another-simulation"
    else:
        result["iteration_id"] = "another-iteration"
    with pytest.raises(ValueError, match="identity mismatch"):
        select(evidence)


@pytest.mark.parametrize("problem", ["duplicate_case", "duplicate_persona", "missing_seed"])
def test_ambiguous_or_missing_persona_join_is_rejected(evidence, problem):
    _, _, iteration, seeds, _ = evidence
    if problem == "missing_seed":
        seeds.clear()
    else:
        row = dict(iteration["runs"][0])
        if problem == "duplicate_persona":
            row["personaName"] = "CASE-2 Another case"
        iteration["runs"].append(row)
    with pytest.raises(ValueError, match="join"):
        select(evidence)


@pytest.mark.parametrize("customer_id", ["../../secret", "c_../secret", None, {}, ""])
def test_unsafe_customer_identifier_never_becomes_a_request_path(evidence, customer_id):
    evidence[3]["persona-1"]["customer_id"] = customer_id
    with pytest.raises(ValueError, match="identifier"):
        select(evidence)


def test_shared_customer_identifier_is_rejected(evidence):
    evidence[3]["persona-2"] = {"customer_id": "c_synthetic-1"}
    with pytest.raises(ValueError, match="share"):
        select(evidence)


@pytest.mark.parametrize("field", ["call_id", "simulation_run_id", "simulation_outcome"])
def test_stale_result_join_is_rejected(evidence, field):
    evidence[-1]["rows"][0][field] = "different"
    with pytest.raises(ValueError, match="does not match"):
        select(evidence)


def test_known_call_customer_must_match_seed(evidence):
    evidence[-1]["rows"][0]["customer_id"] = "c_another"
    with pytest.raises(ValueError, match="conflicts"):
        select(evidence)


def test_already_collected_customer_is_not_read_again(evidence):
    evidence[-1]["rows"][0]["customer_id"] = "c_synthetic-1"
    assert select(evidence) == []


def test_duplicate_result_case_is_rejected(evidence):
    evidence[-1]["rows"].append(dict(evidence[-1]["rows"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        select(evidence)
