"""Current product scope must survive mixed speech and follow-up clarification."""

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
    return json.loads((ROOT / "tests/fixtures/b-confirmation-scope-state.json").read_text())


def turn(code: str, values: dict, raw: str, **fields: str) -> tuple[dict, dict]:
    result = execute_python(code, {"vars": {**values, "turn_raw": raw, **fields}})["vars"]
    return result, json.loads(result["state"])


def mixed_correction(code: str, variables: dict) -> tuple[dict, dict]:
    return turn(
        code,
        variables,
        "Yes for the card and personal loan; "
        "not Friday for the car—Thursday the fifteenth instead.",
        turn_scope="unclear",
        turn_date="2026-10-15",
        turn_date_words="Thursday the fifteenth",
        turn_decision="accept",
    )


def test_unparsed_mixed_proposal_does_not_assume_every_mentioned_product(
    code: str, variables: dict
) -> None:
    result, state = mixed_correction(code, variables)
    assert result["route"] == "dialogue" and state["phase"] == "clarify_financial"
    assert [p["date"] for p in state["products"]] == ["2026-10-16"] * 3
    held = state["unprocessed_proposal"]["clauses"]
    assert len(held) == 1 and held[0]["scope"] == ""
    assert not held[0]["consent_evidence"]


def test_scope_answer_applies_retained_date_only_to_car(code: str, variables: dict) -> None:
    pending, _ = mixed_correction(code, variables)
    result, state = turn(
        code,
        pending,
        "I can pay seventy-five for the car",
        turn_decision="accept",
    )
    assert [p["date"] for p in state["products"]] == [
        "2026-10-16",
        "2026-10-16",
        "2026-10-15",
    ]
    assert state["phase"] == "confirm" and result["route"] == "dialogue"
    assert not any(p["confirmed"] for p in state["products"])


def test_shared_evidence_does_not_expand_explicit_clause_scopes(code: str, variables: dict) -> None:
    evidence = (
        "the card for two hundred and twenty and the personal loan for two hundred "
        "and thirteen are for Friday the sixteenth of October"
    )
    clauses = [
        {
            "scope": scope,
            "decision": "accept",
            "amount": amount,
            "date_words": "Friday the sixteenth of October",
            "evidence": evidence,
        }
        for scope, amount in [("the card", "220"), ("the personal loan", "213")]
    ]
    result, state = turn(
        code,
        variables,
        "No, " + evidence + ", and the",
        turn_terms=json.dumps(clauses),
        turn_date_words="Friday the sixteenth of October",
        turn_decision="reject",
    )
    assert [p["amount"] for p in state["products"]] == [220.0, 213.0, 75.0]
    assert [p["date"] for p in state["products"]] == ["2026-10-16"] * 3
    assert result["route"] == "dialogue"
    assert not any(p["confirmed"] for p in state["products"])


@pytest.fixture
def mixed_variables() -> dict:
    return json.loads((ROOT / "tests/fixtures/b-mixed-disposition-state.json").read_text())


@pytest.mark.parametrize("structured", [False, True])
def test_explicit_dispositions_replace_ambiguous_pending_references(
    code: str, mixed_variables: dict, structured: bool
) -> None:
    paid = "The credit card was paid in the app this morning"
    proposal = "I can pay the personal loan on Friday 16 October"
    fields = {"turn_decision": "reject"}
    if structured:
        fields["turn_terms"] = json.dumps(
            [
                {"scope": "credit card", "disposition": "paid", "evidence": paid},
                {
                    "scope": "personal loan",
                    "date_words": "Friday 16 October",
                    "evidence": proposal,
                },
            ]
        )
    result, state = turn(code, mixed_variables, paid + ", and " + proposal + ".", **fields)
    card, loan = state["products"]
    assert card["status"] == "excluded" and card["reason"] == "paid"
    assert loan["status"] == "agreed" and loan["date"] == "2026-10-16"
    assert loan["amount"] == 900.0 and not loan["confirmed"]
    assert state["pending_clauses"] == [] and state["phase"] == "confirm"
    assert result["route"] == "dialogue"
    assert "one business day" in result["message"]


def test_product_clarification_preserves_new_date_in_same_answer(
    code: str, mixed_variables: dict
) -> None:
    pending, _ = turn(
        code,
        mixed_variables,
        "The credit card.",
        turn_scope="credit card",
    )
    proposal = "I will pay 900.00 for the personal loan on Thursday 15 October."
    result, state = turn(
        code,
        pending,
        proposal,
        turn_scope="personal loan",
        turn_terms=json.dumps(
            [
                {
                    "scope": "personal loan",
                    "amount": "900.00",
                    "date_words": "Thursday 15 October",
                    "evidence": proposal,
                }
            ]
        ),
    )
    card, loan = state["products"]
    assert card["status"] == "excluded" and card["reason"] == "paid"
    assert loan["date"] == "2026-10-15" and loan["amount"] == 900.0
    assert state["phase"] == "confirm" and result["route"] == "dialogue"
    assert not loan["confirmed"]


@pytest.mark.parametrize(
    ("raw", "withdrawn"),
    [
        ("Yes. Actually, do not record the personal loan. I withdraw that proposal.", 1),
        ("Yes, all three. Actually, do not record the car loan. I withdraw that proposal.", 2),
    ],
)
def test_same_turn_withdrawal_cannot_authorize_the_named_loan(
    code: str, variables: dict, raw: str, withdrawn: int
) -> None:
    result, state = turn(
        code,
        variables,
        raw,
        turn_decision="accept",
    )
    assert result["route"] != "create_promise"
    assert not state["products"][withdrawn]["confirmed"]
    assert state["products"][withdrawn]["status"] != "agreed"
