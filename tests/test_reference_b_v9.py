"""Aggregate refusal must preserve independent scoped proposals and negotiation."""

import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["v9", "v10"])
def code(request):
    path = ROOT / f"benchmarks/development-references-{request.param}/multi-product.json"
    workflow = json.loads(path.read_text())
    return workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]


def initial(code, name):
    values = json.loads((ROOT / "tests/fixtures" / name).read_text())
    return execute_python(code, {"vars": values})["vars"]


def test_aggregate_decline_and_product_interest_progress_without_phantom_missing_terms(code):
    result = initial(code, "b-aggregate-decline-and-interest.json")
    state = json.loads(result["state"])
    assert not state.get("unprocessed_proposal")
    assert state["phase"] == "negotiate"
    assert result["route"] == "dialogue"
    assert not any(p.get("confirmed") for p in state["products"])
    assert all(p["status"] != "recorded" for p in state["products"])


def test_aggregate_decline_does_not_erase_independent_future_proposal(code):
    result = initial(code, "b-aggregate-decline-and-proposal.json")
    state = json.loads(result["state"])
    assert not state.get("unprocessed_proposal")
    card = state["products"][0]
    assert (card["amount"], card["date"]) == (510, "2026-10-16")
    assert not card["confirmed"]
    assert result["route"] != "create_promise"


def test_two_currency_clauses_keep_internal_number_conjunctions(code):
    result = initial(code, "b-aggregate-decline-and-proposal.json")
    result.update(
        turn_raw=(
            "I can pay one thousand three hundred and twenty dollars for the personal loan "
            "on Friday sixteen October and one hundred and forty dollars for the car loan "
            "on Friday sixteen October."
        ),
        turn_terms="",
        turn_decision="reject",
        turn_scope="",
        turn_amount="",
        turn_date="",
    )
    result = execute_python(code, {"vars": result})["vars"]
    state = json.loads(result["state"])
    assert not state.get("unprocessed_proposal")
    assert [(p["amount"], p["date"]) for p in state["products"]] == [
        (510, "2026-10-16"),
        (1320, "2026-10-16"),
        (140, "2026-10-16"),
    ]
    assert result["route"] == "dialogue"
    assert state["phase"] == "confirm"
    assert not any(p.get("confirmed") for p in state["products"])


def test_changed_unknown_spoken_amount_still_requires_clarification(code):
    values = json.loads((ROOT / "tests/fixtures/b-aggregate-decline-and-proposal.json").read_text())
    values["turn_raw"] = values["turn_raw"].replace("five hundred and ten", "four hundred and ten")
    result = execute_python(code, {"vars": values})["vars"]
    state = json.loads(result["state"])
    assert state["phase"] == "clarify_financial"
    assert not any(p.get("confirmed") for p in state["products"])
    assert result["route"] != "create_promise"
