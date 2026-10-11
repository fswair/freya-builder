"""Retained proposal completion must preserve product scope and require new consent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10"])
def code(request: pytest.FixtureRequest) -> str:
    workflow = json.loads(
        (ROOT / f"benchmarks/development-references-{request.param}/multi-product.json").read_text()
    )
    return workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]


@pytest.fixture
def variables() -> dict:
    return json.loads((ROOT / "tests/fixtures/b-partial-proposal-state.json").read_text())


def turn(
    code: str,
    variables: dict,
    raw: str,
    scope: str = "",
    structured: bool = True,
    date_words: str | None = None,
) -> tuple[dict, dict]:
    values = {**variables, "turn_raw": raw, "turn_decision": "none"}
    if structured:
        values["turn_terms"] = json.dumps(
            [
                {
                    "scope": scope or "car loan",
                    "date_words": raw if date_words is None else date_words,
                    "evidence": raw,
                    "decision": "none",
                }
            ]
        )
    result = execute_python(code, {"vars": values})["vars"]
    return result, json.loads(result["state"])


@pytest.mark.parametrize("structured", [False, True])
def test_date_only_answer_completes_retained_proposal(
    code: str, variables: dict, structured: bool
) -> None:
    result, state = turn(code, variables, "Friday 16 October", structured=structured)
    assert state["phase"] == "confirm"
    assert len(state["confirmation_batch"]) == 3
    assert state["products"][2]["date"] == "2026-10-16"
    assert result["route"] == "dialogue"
    assert not any(p["confirmed"] for p in state["products"])


def test_new_date_replaces_stale_retained_date(code: str, variables: dict) -> None:
    result, state = turn(code, variables, "Monday 19 October")
    assert state["phase"] == "confirm" and result["route"] == "dialogue"
    assert state["products"][2]["date"] == "2026-10-19"
    assert [p["date"] for p in state["products"][:2]] == ["2026-10-16", "2026-10-16"]
    assert not state["products"][2]["confirmed"]


def test_new_evidence_is_checked_against_current_turn(code: str, variables: dict) -> None:
    result, state = turn(
        code, variables, "I can pay that on Friday 16 October.", date_words="Friday 16 October"
    )
    assert state["phase"] == "confirm" and result["route"] == "dialogue"
    assert state["products"][2]["date"] == "2026-10-16"


def test_unrelated_question_does_not_complete_a_missing_date(code: str, variables: dict) -> None:
    state = json.loads(variables["state"])
    held = state["unprocessed_proposal"]["clauses"][0]
    held.update(evidence="I would like to pay that on", date="", date_words="", date_kind="")
    variables["state"] = json.dumps(state)
    result, state = turn(
        code, variables, "What happens when you record a promise?", structured=False
    )
    assert state["phase"] == "clarify_financial" and result["route"] == "dialogue"
    assert not any(p["confirmed"] for p in state["products"])


def test_another_products_date_does_not_complete_active_product(code: str, variables: dict) -> None:
    for _ in range(3):
        variables, state = turn(code, variables, "Monday 19 October", scope="personal loan")
        assert state["phase"] == "clarify_financial"
        assert state["products"][2]["date"] == "2026-10-16"
        assert not state["products"][2]["confirmed"]
        assert len(state["unprocessed_proposal"]["clauses"]) <= 2


def test_bare_yes_does_not_fill_unresolved_date(code: str, variables: dict) -> None:
    state = json.loads(variables["state"])
    held = state["unprocessed_proposal"]["clauses"][0]
    held.update(evidence="I would like to pay that on", date="", date_words="", date_kind="")
    variables["state"] = json.dumps(state)
    result, state = turn(code, variables, "Yes, I confirm", structured=False)
    assert result["route"] != "create_promise"
    assert state["phase"] == "clarify_financial"
    assert not any(p["confirmed"] for p in state["products"])
