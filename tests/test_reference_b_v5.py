"""Complete product restatements supersede stale fragments without authorizing a write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["v5", "v6", "v7", "v8", "v9", "v10"])
def code(request: pytest.FixtureRequest) -> str:
    workflow = json.loads(
        (ROOT / f"benchmarks/development-references-{request.param}/multi-product.json").read_text()
    )
    return workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]


def replay(code: str, **overrides: str) -> tuple[dict, dict]:
    values = json.loads((ROOT / "tests/fixtures/b-complete-restatement-turn.json").read_text())
    result = execute_python(code, {"vars": {**values, **overrides}})["vars"]
    return result, json.loads(result["state"])


def test_full_restatement_clears_stale_fragments_and_gets_fresh_consent(code: str) -> None:
    result, state = replay(code)
    assert result["route"] == "dialogue"
    assert state["phase"] == "confirm"
    assert not state.get("unprocessed_proposal")
    assert not state["pending_clauses"]
    assert [(p["amount"], p["date"]) for p in state["products"]] == [
        (220.0, "2026-10-16"),
        (213.0, "2026-10-16"),
        (75.0, "2026-10-15"),
    ]
    assert not any(p.get("confirmed") for p in state["products"])
    result.update(turn_raw="Yes, I confirm all of them.", turn_decision="accept")
    result = execute_python(code, {"vars": result})["vars"]
    assert result["route"] == "create_promise"


def test_changed_unextracted_word_amount_is_not_replaced_by_old_amount(code: str) -> None:
    values = json.loads((ROOT / "tests/fixtures/b-complete-restatement-turn.json").read_text())
    result, state = replay(
        code,
        turn_raw=values["turn_raw"].replace("seventy-five dollars", "seventy dollars"),
    )
    assert result["route"] != "create_promise"
    assert state["phase"] == "clarify_financial"
    assert not any(p.get("confirmed") for p in state["products"])


def test_labelled_json_terms_preserve_paid_claim_and_scoped_date(code: str) -> None:
    values = json.loads((ROOT / "tests/fixtures/b-labelled-terms-turn.json").read_text())
    result = execute_python(code, {"vars": values})["vars"]
    state = json.loads(result["state"])
    assert result["route"] == "dialogue"
    assert state["phase"] == "confirm"
    assert not state["pending_clauses"]
    assert not state.get("unprocessed_proposal")
    assert state["products"][0]["reason"] == "paid"
    assert state["products"][1]["amount"] == 900
    assert state["products"][1]["date"] == "2026-10-16"
    assert "one business day" in result["message"]
    assert not any(p.get("confirmed") for p in state["products"])


@pytest.mark.parametrize(
    "replacement",
    [
        "and the personal loan is for Thursday",
        "and the credit card is for Friday",
        "and I refuse the personal loan for Friday",
    ],
)
def test_uncovered_date_correction_is_not_dismissed_as_repetition(
    code: str, replacement: str
) -> None:
    values = json.loads((ROOT / "tests/fixtures/b-labelled-terms-turn.json").read_text())
    values["turn_raw"] = values["turn_raw"].replace(
        "and the personal loan is for Friday", replacement
    )
    result = execute_python(code, {"vars": values})["vars"]
    state = json.loads(result["state"])
    assert result["route"] != "create_promise"
    assert state["phase"] == "clarify_financial"
    assert state.get("unprocessed_proposal")
    assert not any(p.get("confirmed") for p in state["products"])


def test_partial_restatement_cannot_discard_other_unknown_terms(code: str) -> None:
    raw = "I confirm the credit card for two hundred and twenty dollars on October sixteenth."
    result, state = replay(
        code,
        turn_raw=raw,
    )
    assert result["route"] != "create_promise"
    assert state.get("unprocessed_proposal")


def test_harmless_yes_prefix_does_not_become_a_second_incomplete_clause(code: str) -> None:
    values = json.loads((ROOT / "tests/fixtures/b-truncated-confirm-turn.json").read_text())
    values.update(
        turn_raw="Yes, I confirm two hundred and twenty dollars for",
        turn_terms=json.dumps(
            [
                {
                    "scope": "",
                    "decision": "accept",
                    "amount": "220",
                    "evidence": "I confirm two hundred and twenty dollars for",
                }
            ]
        ),
    )
    result = execute_python(code, {"vars": values})["vars"]
    state = json.loads(result["state"])
    assert state["phase"] == "clarify_financial"
    assert len(state["unprocessed_proposal"]["clauses"]) == 1
    assert result["route"] != "create_promise"
