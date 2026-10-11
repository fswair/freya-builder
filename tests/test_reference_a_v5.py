"""Interrupted callback times and possible cents need bounded clarification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["v5", "v6", "v8", "v9"])
def code(request: pytest.FixtureRequest) -> str:
    path = ROOT / f"benchmarks/development-references-{request.param}/single-product.json"
    workflow = json.loads(path.read_text())
    return workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]


def replay(code: str, fixture: str, **overrides: str) -> tuple[dict, dict]:
    values = json.loads((ROOT / "tests/fixtures" / fixture).read_text())
    result = execute_python(code, {"vars": {**values, **overrides}})["vars"]
    return result, json.loads(result["call_state"])


def test_interrupted_clock_does_not_consume_second_invalid_attempt(code: str) -> None:
    result, state = replay(code, "a-interrupted-callback-time.json")
    assert result["next"] == "speak"
    assert state["stage"] == "callback"
    assert state["callback_invalid"] == 1
    assert state["callback_proposed_date"] == "tomorrow"
    assert state["callback_proposed_time"] == ""
    result.update(
        turn_raw="Seven in the evening.",
        turn_callback_time="19:00",
        turn_time="19:00",
        turn_caller="third_party",
        turn_coverage="complete",
    )
    result = execute_python(code, {"vars": result})["vars"]
    assert result["next"] == "callback"
    assert result["callback_date"] == "2026-10-15"
    assert result["callback_time"] == "19:00"


def test_complete_invalid_clock_still_consumes_second_attempt(code: str) -> None:
    result, state = replay(
        code,
        "a-interrupted-callback-time.json",
        turn_raw="Tomorrow at seven in the morning.",
        turn_coverage="complete",
    )
    assert result["next"] == "goodbye"
    assert state["callback_invalid"] == 2
    assert result["reason"] == "no_callback"


def test_cents_completion_keeps_the_existing_date_and_requires_readback(code: str) -> None:
    result, state = replay(code, "a-cents-before-clarification.json")
    product = state["products"]["p1"]
    assert result["next"] == "speak"
    assert product["unresolved_slot"] == "amount"
    assert product["attempts"] == 0
    assert product["amount"] == 637.5
    assert product["date"] == "2026-10-16"
    result.update(
        turn_raw="I can do six hundred thirty-seven fifty.",
        turn_amount="637.50",
        turn_intent="proposal",
        turn_coverage="complete",
    )
    result = execute_python(code, {"vars": result})["vars"]
    product = json.loads(result["call_state"])["products"]["p1"]
    assert result["next"] == "confirm_speak"
    assert product["readback"] == "637.5|2026-10-16"
    assert not product["confirmed"]


def test_explicit_decimal_below_floor_is_not_treated_as_cut_off(code: str) -> None:
    result, state = replay(
        code,
        "a-cents-before-clarification.json",
        turn_raw="I can pay 637.00.",
    )
    product = state["products"]["p1"]
    assert product["attempts"] > 0
    assert not product.get("cents_clarified")
    assert result["next"] != "create"


def test_cents_clarification_is_only_asked_once(code: str) -> None:
    result, _ = replay(code, "a-cents-before-clarification.json")
    result.update(
        turn_raw="I mean six hundred thirty-seven dollars, no cents.",
        turn_amount="637",
        turn_intent="proposal",
        turn_coverage="complete",
    )
    result = execute_python(code, {"vars": result})["vars"]
    product = json.loads(result["call_state"])["products"]["p1"]
    assert product["attempts"] > 0
    assert result["next"] != "create"
