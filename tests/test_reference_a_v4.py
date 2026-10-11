"""Questions and interrupted currency amounts preserve the pending payment terms."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["v4", "v5", "v6", "v8", "v9"])
def code(request: pytest.FixtureRequest) -> str:
    reference = ROOT / f"benchmarks/development-references-{request.param}/single-product.json"
    workflow = json.loads(reference.read_text())
    return workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]


def replay(code: str, fixture: str) -> tuple[dict, dict]:
    values = json.loads((ROOT / "tests/fixtures" / fixture).read_text())
    result = execute_python(code, {"vars": values})["vars"]
    return result, json.loads(result["call_state"])["products"]["p1"]


def test_minimum_question_does_not_invent_an_unresolved_proposal(code: str) -> None:
    result, product = replay(code, "a-minimum-question-turn.json")
    assert result["next"] == "speak"
    assert not product["terms_unresolved"]
    assert not product.get("unresolved_slot")
    assert product["amount"] == 637.5
    assert product["attempts"] == 0
    result.update(
        turn_raw="I can do that this Friday.",
        turn_intent="proposal",
        turn_decision="accept",
        turn_date="this Friday",
        turn_coverage="complete",
    )
    result = execute_python(code, {"vars": result})["vars"]
    product = json.loads(result["call_state"])["products"]["p1"]
    assert result["next"] == "confirm_speak"
    assert product["readback"] == "637.5|2026-10-16"


def test_below_floor_proposal_never_escalates_back_to_full_balance(code: str) -> None:
    result, product = replay(code, "a-below-floor-followup-turn.json")
    assert result["next"] != "create"
    assert product["amount"] == product["floor"] == 637.5
    assert product["full_too_much"]
    assert not product["confirmed"]


def test_currency_and_fragment_asks_for_amount_completion(code: str) -> None:
    result, product = replay(code, "a-incomplete-cents-turn.json")
    assert result["next"] == "speak"
    assert product["unresolved_slot"] == "amount"
    assert product["unprocessed"]["amount"] == "637"
    result.update(
        turn_raw="16 October.",
        turn_intent="proposal",
        turn_date="16 October",
        turn_coverage="complete",
    )
    result = execute_python(code, {"vars": result})["vars"]
    product = json.loads(result["call_state"])["products"]["p1"]
    assert result["next"] == "speak"
    assert product["unresolved_slot"] == "amount"
    assert not product["readback"]
    assert not product["confirmed"]
