"""Spoken cents must preserve canonical amount and fresh confirmation semantics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["v3", "v4", "v5", "v6", "v8", "v9"])
def code(request: pytest.FixtureRequest) -> str:
    reference = ROOT / f"benchmarks/development-references-{request.param}/single-product.json"
    workflow = json.loads(reference.read_text())
    return workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]


def replay(code: str, words: str, amount: str = "637.50") -> tuple[dict, dict]:
    values = json.loads((ROOT / "tests/fixtures/a-spoken-cents-turn.json").read_text())
    values.update(
        turn_raw=f"Yes, I confirm {words} on sixteen October.",
        turn_amount=amount,
    )
    result = execute_python(code, {"vars": values})["vars"]
    return result, json.loads(result["call_state"])["products"]["p1"]


@pytest.mark.parametrize(
    "words",
    [
        "six hundred and thirty-seven fifty",
        "six hundred thirty seven dollars and fifty cents",
        "six hundred and thirty-seven point five zero",
        "637.50",
    ],
)
def test_equivalent_cents_amount_can_confirm_unchanged_readback(code: str, words: str) -> None:
    result, product = replay(code, words)
    assert result["next"] == "create"
    assert product["amount"] == 637.5
    assert product["confirmed"] == "637.5|2026-10-16"


def test_colloquial_cents_cannot_ground_an_incorrect_integer_sum(code: str) -> None:
    result, product = replay(code, "six hundred and thirty-seven fifty", "687")
    assert result["next"] != "create"
    assert product["amount"] == 637.5
    assert not product["confirmed"]


def test_regular_hundreds_stay_integer_and_changed_terms_need_new_readback(code: str) -> None:
    result, product = replay(code, "six hundred and fifty", "650")
    assert result["next"] != "create"
    assert product["amount"] == 650
    assert not product["confirmed"]


def test_amount_completion_survives_followup_date_clarification(code: str) -> None:
    values = json.loads((ROOT / "tests/fixtures/a-amount-completion-turn.json").read_text())
    result = execute_python(code, {"vars": values})["vars"]
    product = json.loads(result["call_state"])["products"]["p1"]
    assert product["amount"] == 540
    assert product["need_exact"]
    assert product["unresolved_slot"] != "amount"
    result.update(
        turn_raw="Monday the twenty-sixth.",
        turn_date="Monday the twenty-sixth",
        turn_intent="proposal",
        turn_coverage="complete",
        turn_missing="none",
    )
    result = execute_python(code, {"vars": result})["vars"]
    product = json.loads(result["call_state"])["products"]["p1"]
    assert product["date"] == "2026-10-27"
    assert product["amount"] == 540
    assert result["next"] != "create"
    assert "business day" in result["instruction"]


def test_unresolved_amount_survives_ungrounded_completion(code: str) -> None:
    values = json.loads((ROOT / "tests/fixtures/a-amount-completion-turn.json").read_text())
    values.update(turn_raw="I do not know the amount.", turn_amount="540")
    result = execute_python(code, {"vars": values})["vars"]
    product = json.loads(result["call_state"])["products"]["p1"]
    assert product["unresolved_slot"] == "amount"
    assert not product["confirmed"]
    assert result["next"] != "create"
