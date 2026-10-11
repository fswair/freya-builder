"""Currency amounts cannot become calendar dates or prevent a completed proposal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["v4", "v5", "v6", "v7", "v8", "v9", "v10"])
def code(request: pytest.FixtureRequest) -> str:
    workflow = json.loads(
        (ROOT / f"benchmarks/development-references-{request.param}/multi-product.json").read_text()
    )
    return workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]


def replay(code: str, **overrides: str) -> tuple[dict, dict]:
    values = json.loads((ROOT / "tests/fixtures/b-completed-money-date-turn.json").read_text())
    result = execute_python(code, {"vars": {**values, **overrides}})["vars"]
    return result, json.loads(result["state"])


@pytest.mark.parametrize("structured", [True, False])
def test_complete_money_and_date_resolves_retained_product(code: str, structured: bool) -> None:
    result, state = replay(code, **({} if structured else {"turn_terms": "[]"}))
    assert result["route"] == "dialogue"
    assert state["phase"] == "confirm"
    assert not state["pending_clauses"]
    assert not state.get("unprocessed_proposal")
    assert [(p["amount"], p["date"]) for p in state["products"]] == [
        (220.0, "2026-10-16"),
        (213.0, "2026-10-23"),
        (75.0, "2026-10-16"),
    ]
    assert not any(p.get("confirmed") for p in state["products"])
    assert result["message"].endswith("Do you confirm?")


def test_conflicting_weekday_still_needs_clarification(code: str) -> None:
    result, state = replay(
        code,
        turn_raw="I will pay the 75.00 for the car loan on Monday 16 October.",
        turn_date_words="Monday 16 October",
        turn_terms="[]",
    )
    assert result["route"] != "create_promise"
    assert state["phase"] != "confirm"
    assert not any(p.get("confirmed") for p in state["products"])


def test_amount_without_completed_date_does_not_inherit_extractor_date(code: str) -> None:
    result, state = replay(
        code,
        turn_raw="I will pay the 75.00 for the car loan on",
        turn_terms="[]",
    )
    assert result["route"] != "create_promise"
    assert state["phase"] != "confirm"
    assert state.get("unprocessed_proposal")


def test_multiple_paid_products_do_not_duplicate_the_same_notice(code: str) -> None:
    values = json.loads((ROOT / "tests/fixtures/b-paid-dispute-turn.json").read_text())
    values.update(
        turn_raw="I paid the card arrears today. I paid the personal loan arrears today.",
        turn_terms=json.dumps(
            [
                {"scope": "card", "evidence": "I paid the card arrears today"},
                {"scope": "personal loan", "evidence": "I paid the personal loan arrears today"},
            ]
        ),
        turn_scope="",
        turn_topics="paid",
    )
    result = execute_python(code, {"vars": values})["vars"]
    state = json.loads(result["state"])
    assert all(p["reason"] == "paid" for p in state["products"])
    for _ in state["products"]:
        if result["route"] != "add_note":
            break
        result.update(event="add_note", api_data=json.dumps({"ok": True}))
        result = execute_python(code, {"vars": result})["vars"]
    assert result["route"] == "farewell"
    spoken = result.get("farewell", "") + result.get("message", "")
    assert spoken.count("Payments can take one business day to appear.") == 1


def test_all_today_refusal_preserves_independent_card_proposal(code: str) -> None:
    values = json.loads((ROOT / "tests/fixtures/b-all-today-and-card-turn.json").read_text())
    result = execute_python(code, {"vars": values})["vars"]
    state = json.loads(result["state"])
    card = state["products"][0]
    assert (card["status"], card["amount"], card["date"]) == (
        "agreed",
        510.0,
        "2026-10-16",
    )
    assert not state.get("unprocessed_proposal")
    assert state["phase"] == "negotiate"
    assert "personal loan" in result["message"]
    assert result["route"] != "create_promise"


def test_unscoped_negative_date_asks_a_targeted_question_and_stays_unresolved(code: str) -> None:
    values = json.loads((ROOT / "tests/fixtures/b-truncated-confirm-turn.json").read_text())
    result = execute_python(code, {"vars": values})["vars"]
    state = json.loads(result["state"])
    assert "should not be paid on" in result["message"]
    assert any(c.get("date_conflict") for c in state["unprocessed_proposal"]["clauses"])
    result.update(
        turn_raw="The car loan.",
        turn_scope="car loan",
        turn_terms=json.dumps([{"scope": "car loan", "evidence": "The car loan."}]),
    )
    result = execute_python(code, {"vars": result})["vars"]
    state = json.loads(result["state"])
    assert result["route"] != "create_promise"
    assert not any(p.get("confirmed") for p in state["products"])
    assert state["phase"] != "confirm"


def test_explicit_restructuring_request_survives_missing_topic_extraction(code: str) -> None:
    values = json.loads((ROOT / "tests/fixtures/b-explicit-restructuring-turn.json").read_text())
    result = execute_python(code, {"vars": values})["vars"]
    assert result["route"] == "transfer_to_agent"
    assert result["request_queue"] == "restructuring"
    state = json.loads(result["state"])
    assert all(p["status"] != "recorded" for p in state["products"])


@pytest.mark.parametrize(
    "raw",
    [
        "I do not want lower instalments.",
        "If I want lower instalments, what would happen?",
    ],
)
def test_negated_or_hypothetical_restructuring_does_not_force_transfer(code: str, raw: str) -> None:
    values = json.loads((ROOT / "tests/fixtures/b-explicit-restructuring-turn.json").read_text())
    values.update(turn_raw=raw, turn_decision="none", turn_topics="")
    result = execute_python(code, {"vars": values})["vars"]
    assert result["route"] != "transfer_to_agent"
