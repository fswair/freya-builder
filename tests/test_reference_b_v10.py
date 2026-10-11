"""Keep numeric corrections product-scoped and honor repeated human requests."""

import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def code():
    workflow = json.loads(
        (ROOT / "benchmarks/development-references-v10/multi-product.json").read_text()
    )
    return workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]


def run(code, fixture, raw=None):
    values = json.loads((ROOT / "tests/fixtures" / f"{fixture}.json").read_text())
    if raw is not None:
        values.update({key: "" for key in values if key.startswith("turn_")})
        values["turn_raw"] = raw
    result = execute_python(code, {"vars": values})["vars"]
    return result, json.loads(result["state"])


def test_unscoped_clarification_never_borrows_the_whole_previous_batch(code):
    result, state = run(code, "b-ambiguous-amount-after-scoped-date")
    assert [p["amount"] for p in state["products"]] == [220, 213, 75]
    assert state["phase"] == "resolve_product"
    assert result["route"] != "create_promise"
    assert not any(p.get("confirmed") for p in state["products"])


def test_partial_date_does_not_copy_card_amount_to_personal_loan(code):
    result, state = run(code, "b-incomplete-multi-product-correction")
    assert [p["amount"] for p in state["products"]] == [220, 213, 75]
    assert "exact date" in result["message"].lower()
    assert "personal loan" in result["message"].lower()
    assert result["route"] != "create_promise"


def test_complete_numeric_restatement_updates_each_product_independently(code):
    result, state = run(
        code,
        "b-incomplete-multi-product-correction",
        "No, the credit card should be 220.00 on 16 October, "
        "the personal loan is 230.00 on 16 October, the car loan is 75.00 on 15 October.",
    )
    assert [p["amount"] for p in state["products"]] == [220, 230, 75]
    assert [p["date"] for p in state["products"]] == [
        "2026-10-16",
        "2026-10-16",
        "2026-10-15",
    ]
    assert state["phase"] == "confirm"
    assert result["route"] != "create_promise"
    assert not any(p.get("confirmed") for p in state["products"])


def test_repeated_request_after_help_offer_transfers_without_second_offer(code):
    result, _ = run(code, "b-insistence-after-human-offer")
    assert result["route"] == "transfer_to_agent"


def test_explicit_acceptance_after_help_offer_is_not_forced_to_transfer(code):
    result, _ = run(code, "b-insistence-after-human-offer", "Yes, please help me.")
    assert result["route"] != "transfer_to_agent"
