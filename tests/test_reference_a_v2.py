"""Synthetic controller contracts; these do not measure Freya extraction or speech."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["v2", "v3", "v4", "v5", "v6", "v8", "v9"])
def reference(request: pytest.FixtureRequest) -> Path:
    return ROOT / f"benchmarks/development-references-{request.param}/single-product.json"


@pytest.fixture
def code(reference: Path) -> str:
    workflow = json.loads(reference.read_text())
    return workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]


@pytest.fixture
def variables(code: str) -> dict:
    values = execute_python(
        code, {"vars": {"now": "2026-10-14T11:00", "customer_id": "synthetic-customer"}}
    )["vars"]
    state = json.loads(values["call_state"])
    state.update(
        stage="negotiate",
        verified=True,
        dob_pass=True,
        phone_pass=True,
        caller_status="customer_claimed",
        product_id="p1",
    )
    state["products"] = {
        "p1": {
            "product_id": "p1",
            "type": "credit_card",
            "card_last4": "7740",
            "label": "credit card ending 7740",
            "past_due": 365.0,
            "floor": 109.5,
            "days_late": 8,
            "deadline": "2026-10-23",
            "budget": 2,
            "attempts": 0,
            "floor_offered": False,
            "full_too_much": False,
            "amount": 365.0,
            "date": "2026-10-14",
            "recorded": False,
            "confirmed": "",
            "readback": "",
            "create_attempts": 0,
            "refused_offers": [],
            "need_exact": False,
            "terms_unresolved": False,
        }
    }
    values["call_state"] = json.dumps(state)
    return values


def turn(code: str, values: dict, raw: str, **fields: str) -> tuple[dict, dict, dict]:
    values = {
        **values,
        "turn_raw": raw,
        "turn_coverage": "complete",
        "turn_missing": "none",
        **fields,
    }
    result = execute_python(code, {"vars": values})["vars"]
    state = json.loads(result["call_state"])
    return result, state, state["products"]["p1"]


def test_hardship_precedes_uncertain_affordability(code: str, variables: dict) -> None:
    result, state, product = turn(
        code,
        variables,
        "I lost my job last month and I cannot pay anything. Can I get a discount?",
        turn_hardship="yes",
        turn_full_unaffordable="yes",
        turn_intent="too_much",
        turn_decision="reject",
        turn_question="discount",
    )
    assert state["stage"] == "support"
    assert state["hardship"] is True
    assert product["attempts"] == 0 and not product["floor_offered"]
    assert "sorry" in result["instruction"].lower()


def test_support_decline_with_question_does_not_reopen_payment(code: str, variables: dict) -> None:
    state = json.loads(variables["call_state"])
    state.update(stage="support", hardship=True)
    variables["call_state"] = json.dumps(state)
    result, state, product = turn(
        code,
        variables,
        "No, I do not want support. I cannot pay anything. Can you give a discount?",
        turn_decision="reject",
        turn_full_unaffordable="yes",
        turn_question="discount",
    )
    assert result["next"] == "goodbye"
    assert state["terminal_selected"] is True
    assert product["attempts"] == 0


@pytest.mark.parametrize("extracted", ["this Friday", "2026-10-16"])
def test_negated_first_sentence_does_not_erase_positive_date(
    code: str, variables: dict, extracted: str
) -> None:
    result, state, product = turn(
        code,
        variables,
        "I cannot pay that today. I can pay the full amount this Friday.",
        turn_date=extracted,
        turn_amount_reference="full",
        turn_decision="reject",
        turn_intent="proposal",
    )
    assert state["stage"] == "confirm" and result["next"] == "confirm_speak"
    assert product["date"] == "2026-10-16" and product["amount"] == 365.0
    assert not product["confirmed"]


@pytest.mark.parametrize(
    "date_words", ["today, 14 October", "today, October fourteenth", "2026-10-14"]
)
def test_equivalent_date_expressions_share_one_day(
    code: str, variables: dict, date_words: str
) -> None:
    result, state, product = turn(
        code,
        variables,
        "I can pay the full amount today, 14 October.",
        turn_date=date_words,
        turn_amount_reference="full",
        turn_intent="proposal",
    )
    assert state["stage"] == "confirm" and result["next"] == "confirm_speak"
    assert product["date"] == "2026-10-14"


def test_sentence_boundary_preserves_decimal_amount(code: str, variables: dict) -> None:
    result, state, product = turn(
        code,
        variables,
        "I cannot pay today. I can pay 200.50 this Friday.",
        turn_amount="200.50",
        turn_date="this Friday",
        turn_intent="proposal",
    )
    assert state["stage"] == "confirm" and result["next"] == "confirm_speak"
    assert product["amount"] == 200.5 and product["date"] == "2026-10-16"


def test_hardship_keeps_voluntary_proposal(code: str, variables: dict) -> None:
    result, state, product = turn(
        code,
        variables,
        "I lost my job. I can still pay the full amount this Friday.",
        turn_hardship="yes",
        turn_date="this Friday",
        turn_amount_reference="full",
        turn_intent="proposal",
    )
    assert state["hardship"] is True and state["stage"] == "confirm"
    assert product["date"] == "2026-10-16" and product["attempts"] == 0
    assert result["next"] == "confirm_speak"


def confirmed_offer(code: str, variables: dict) -> dict:
    result, state, _ = turn(
        code,
        variables,
        "I can pay the full amount this Friday.",
        turn_date="this Friday",
        turn_amount_reference="full",
        turn_intent="proposal",
    )
    assert state["stage"] == "confirm"
    return result


def test_short_yes_authorizes_unchanged_readback(code: str, variables: dict) -> None:
    pending = confirmed_offer(code, variables)
    result, _, product = turn(code, pending, "Yes, I confirm.", turn_decision="accept")
    assert result["next"] == "create"
    assert product["confirmed"] == product["readback"]
    assert product["date"] == "2026-10-16"


@pytest.mark.parametrize(
    "raw",
    [
        "Are you recording the promise now?",
        "You are recording this promise, correct?",
        "If I say yes, will you record it?",
    ],
)
def test_process_question_is_not_financial_consent(code: str, variables: dict, raw: str) -> None:
    pending = confirmed_offer(code, variables)
    result, _, product = turn(code, pending, raw, turn_decision="accept")
    assert result["next"] != "create" and not product["confirmed"]


def test_changed_terms_need_fresh_confirmation(code: str, variables: dict) -> None:
    pending = confirmed_offer(code, variables)
    result, state, product = turn(
        code,
        pending,
        "Yes, but make it next Monday.",
        turn_decision="accept",
        turn_date="next Monday",
        turn_intent="proposal",
    )
    assert result["next"] == "confirm_speak" and state["stage"] == "confirm"
    assert product["date"] == "2026-10-19" and not product["confirmed"]


def test_conflicting_date_does_not_fall_back_to_today(code: str, variables: dict) -> None:
    result, state, product = turn(
        code,
        variables,
        "I can pay the full amount today, tomorrow.",
        turn_date="today, tomorrow",
        turn_amount_reference="full",
        turn_intent="proposal",
    )
    assert result["next"] != "create" and state["stage"] != "confirm"
    assert not product["confirmed"]


def test_spoken_amount_with_secondary_question_completes_retained_date(
    code: str, variables: dict
) -> None:
    state = json.loads(variables["call_state"])
    state["products"]["p1"].update(
        past_due=1250.0,
        amount=1250.0,
        unresolved_slot="amount",
        terms_unresolved=True,
        held_date_words="six november",
    )
    variables["call_state"] = json.dumps(state)
    result, state, product = turn(
        code,
        variables,
        "Twelve hundred and fifty. Am I talking to a robot?",
        turn_amount="1250",
        turn_question="ai",
    )
    assert product["amount"] == 1250.0 and product["date"] == "2026-10-23"
    assert product["unresolved_slot"] == ""
    assert state["stage"] == "negotiate" and result["next"] == "speak"
    assert "2026-10-23" in result["instruction"]


def test_comma_affirmation_supplies_amount_without_authorizing_write(
    code: str, variables: dict
) -> None:
    state = json.loads(variables["call_state"])
    state["products"]["p1"].update(unresolved_slot="amount", terms_unresolved=True)
    variables["call_state"] = json.dumps(state)
    result, state, product = turn(
        code, variables, "Yes, I confirm 365.", turn_amount="365", turn_decision="accept"
    )
    assert state["stage"] == "confirm" and result["next"] == "confirm_speak"
    assert product["unresolved_slot"] == "" and not product["confirmed"]


def test_readback_is_fixed_text_from_the_same_terms(
    code: str, variables: dict, reference: Path
) -> None:
    result = confirmed_offer(code, variables)
    workflow = json.loads(reference.read_text())
    assert workflow["nodes"]["confirm_speak"]["on_enter"] == {
        "type": "tts",
        "text": "{{confirmation_text}}",
    }
    assert result["confirmation_text"] == (
        "For your credit card ending 7740, the amount is 365.0 "
        "and the payment date is Friday 16 October 2026. Do you confirm?"
    )


def test_explicit_confirmation_survives_redundant_semantic_label(
    code: str, variables: dict
) -> None:
    pending = confirmed_offer(code, variables)
    result, _, product = turn(
        code,
        pending,
        "Yes, I confirm 365.00 this Friday.",
        turn_amount="365.00",
        turn_amount_reference="full",
        turn_date="this Friday",
        turn_intent="accept",
        turn_decision="accept",
        turn_consent_words="Yes, I confirm",
    )
    assert result["next"] == "create"
    assert product["confirmed"] == "365.0|2026-10-16"


def test_paying_verb_resolves_missing_amount(code: str, variables: dict) -> None:
    state = json.loads(variables["call_state"])
    state["products"]["p1"].update(unresolved_slot="amount", terms_unresolved=True)
    variables["call_state"] = json.dumps(state)
    result, state, product = turn(
        code,
        variables,
        "I am paying the full 365.00 today, 14 October.",
        turn_amount="365.00",
        turn_amount_reference="full",
        turn_date="today, 14 October",
        turn_intent="proposal",
        turn_decision="accept",
    )
    assert state["stage"] == "confirm" and result["next"] == "confirm_speak"
    assert product["unresolved_slot"] == "" and not product["confirmed"]


@pytest.mark.parametrize(
    "raw", ["I paid it in the app this morning.", "It was already paid this morning."]
)
def test_completed_payment_time_is_not_a_future_proposal(
    code: str, variables: dict, raw: str
) -> None:
    result, state, product = turn(
        code, variables, raw, turn_intent="paid", turn_date="this morning"
    )
    assert result["next"] == "goodbye" and state["terminal_selected"] is True
    assert "one business day" in result["closing_message"].lower()
    assert not product["recorded"] and not product["confirmed"]


def test_paid_and_new_proposal_remain_ambiguous(code: str, variables: dict) -> None:
    result, _, product = turn(
        code,
        variables,
        "I already paid it today, but I will pay the full amount tomorrow.",
        turn_intent="paid",
        turn_date="tomorrow",
        turn_amount_reference="full",
    )
    assert result["next"] not in {"goodbye", "create"}
    assert product["unresolved_slot"] == "disposition"


def test_same_turn_withdrawal_overrides_an_initial_yes(code: str, variables: dict) -> None:
    pending = confirmed_offer(code, variables)
    result, _, product = turn(
        code,
        pending,
        "Yes. Actually, do not record that. I withdraw this proposal; "
        "I have not decided the amount or date.",
        turn_decision="accept",
        turn_consent_words="Yes",
    )
    assert result["next"] != "create" and not product["confirmed"]


def test_equivalent_spoken_terms_are_still_confirmation(code: str, variables: dict) -> None:
    pending = confirmed_offer(code, variables)
    result, _, product = turn(
        code,
        pending,
        "Yes, exactly: three hundred and sixty-five for the credit card on "
        "16 October 2026, as you just read back. Nothing has changed.",
        turn_intent="accept",
        turn_decision="accept",
        turn_consent_words="Yes, exactly",
        turn_amount="365",
        turn_date="16 October 2026",
    )
    assert result["next"] == "create"
    assert product["confirmed"] == "365.0|2026-10-16"
