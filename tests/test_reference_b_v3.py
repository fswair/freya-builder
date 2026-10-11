"""Broader-train regressions for scoped dispositions and incomplete consent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10"])
def code(request: pytest.FixtureRequest) -> str:
    workflow = json.loads(
        (ROOT / f"benchmarks/development-references-{request.param}/multi-product.json").read_text()
    )
    return workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]


def replay(code: str, fixture: str, **overrides: str) -> tuple[dict, dict]:
    values = json.loads((ROOT / "tests/fixtures" / fixture).read_text())
    result = execute_python(code, {"vars": {**values, **overrides}})["vars"]
    return result, json.loads(result["state"])


def test_completed_card_and_disputed_historic_loan_stay_distinct(code: str) -> None:
    result, state = replay(code, "b-paid-dispute-turn.json")
    assert result["route"] != "create_promise"
    assert [(p["status"], p["reason"]) for p in state["products"]] == [
        ("excluded", "paid"),
        ("excluded", "dispute"),
    ]
    assert (state["outcome"], state["reason"]) == ("no_promise", "dispute")


@pytest.mark.parametrize(
    "fixture", ["b-truncated-confirm-turn.json", "b-truncated-write-turn.json"]
)
def test_uncovered_trailing_correction_cannot_authorize_writes(code: str, fixture: str) -> None:
    result, state = replay(code, fixture)
    assert result["route"] != "create_promise"
    assert not any(p.get("confirmed") for p in state["products"])
    assert state["phase"] == "clarify_financial"
    assert state.get("unprocessed_proposal")


def test_unqualified_confirmation_still_authorizes_unchanged_batch(code: str) -> None:
    result, state = replay(
        code,
        "b-truncated-confirm-turn.json",
        turn_raw="Yes, I confirm all of them.",
        turn_decision="accept",
        turn_terms="[]",
    )
    assert result["route"] == "create_promise"
    assert all(p.get("confirmed") for p in state["products"])


def test_unscoped_negative_date_cannot_cancel_an_invented_product(code: str) -> None:
    result, state = replay(
        code,
        "b-truncated-confirm-turn.json",
        turn_raw="Yes for the card and personal loan; not Friday.",
        turn_decision="accept",
        turn_terms=json.dumps(
            [
                {
                    "scope": "card, personal loan",
                    "decision": "accept",
                    "evidence": "Yes for the card and personal loan",
                },
                {
                    "scope": "car loan",
                    "decision": "reject",
                    "date_words": "Friday",
                    "evidence": "not Friday",
                },
            ]
        ),
    )
    assert result["route"] != "create_promise"
    assert state["products"][2]["status"] != "excluded"
    assert not any(p.get("confirmed") for p in state["products"])
    assert state["pending_clauses"] or state.get("unprocessed_proposal")


def test_balance_in_product_identification_does_not_undo_retained_paid_claim(code: str) -> None:
    result, state = replay(code, "b-scope-paid-turn.json")
    assert result["route"] != "create_promise"
    card, loan = state["products"]
    assert (card["status"], card["reason"]) == ("excluded", "paid")
    assert loan["status"] == "open"
    assert state["pending_clauses"][0]["date"] == "2026-10-16"


def test_explicit_new_proposal_can_replace_retained_paid_claim(code: str) -> None:
    _, state = replay(
        code,
        "b-scope-paid-turn.json",
        turn_raw="Actually, for the credit card I can pay 100 on Friday 16 October.",
        turn_scope="credit card",
        turn_topics="",
        turn_paid_evidence="",
        turn_terms=json.dumps(
            [
                {
                    "scope": "credit card",
                    "amount": "100",
                    "date_words": "Friday 16 October",
                    "evidence": "for the credit card I can pay 100 on Friday 16 October",
                }
            ]
        ),
    )
    card = state["products"][0]
    assert card["status"] == "agreed"
    assert card["amount"] == 100
    assert card["date"] == "2026-10-16"
    assert not card["confirmed"]
