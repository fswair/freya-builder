"""Build Brief A decision regressions for the recorded Astra state contract.

These are practice-specific fixtures, never policy supplied to the generic compiler.
Extractions are scripted: a passing case cannot prove Freya extracts the utterance correctly.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path


def suite() -> dict:
    product = {
        "product_id": "p1",
        "type": "personal_loan",
        "label": "personal loan",
        "past_due": 900,
        "floor": 300,
        "days_late": 5,
        "deadline": "2026-11-02",
        "budget": 1,
        "attempts": 0,
        "floor_offered": False,
        "full_too_much": False,
        "amount": 900,
        "date": "2026-10-14",
        "recorded": False,
        "confirmed": "",
        "readback": "",
        "create_attempts": 0,
        "refused_offers": [],
        "need_exact": False,
    }
    state: dict = {
        "stage": "negotiate",
        "name_asks": 1,
        "verified": True,
        "dob_pass": True,
        "wrong": 0,
        "security_declines": 0,
        "talk_declines": 0,
        "callback_invalid": 0,
        "human_offered": False,
        "human_pending": False,
        "hardship": False,
        "products": {"p1": product},
        "terminal_selected": False,
        "prompt": "Can you make this payment today?",
        "security_field": "phone_last4",
        "product_id": "p1",
    }
    cases: list[dict] = []

    def case(name: str, *, stage="negotiate", patch=None, product_patch=None) -> dict:
        current = deepcopy(state)
        current["stage"] = stage
        current.update(patch or {})
        current["products"]["p1"].update(product_patch or {})
        result = {
            "id": name,
            "start_node": "speak",
            "variables": {
                "now": "2026-10-14T11:00",
                "customer_id": "offline-customer",
                "title": "Mx",
                "last_name": "Example",
                "call_state": json.dumps(current),
            },
            "responses": [],
            "turns": [],
        }
        cases.append(result)
        return result

    def turn(intent="other", decision="none", **extra) -> dict:
        return {
            "turn_intent": intent,
            "turn_decision": decision,
            "turn_amount": "",
            "turn_date": "",
            "turn_answer": "",
            "turn_numeric_dob": "",
            "turn_time": "",
            "turn_question": "none",
            "turn_hardship": "no",
            "turn_human": "no",
            "turn_no_talk": "no",
            "turn_plan": "no",
            **extra,
        }

    def step(c: dict, extracted: dict, *, stage=None, variables=None, operations=None):
        expect = {"variables": variables or {}}
        if stage:
            expect["variables"]["call_state.stage"] = stage
        if operations is not None:
            expect["operations"] = operations
        c["turns"].append({"extracted": extracted, "expect": expect})

    def close(outcome: str, reason: str) -> dict:
        response = {
            "operation": "close_call",
            "expect_body": {"outcome": outcome, **({"reason": reason} if reason else {})},
            "result": {"data": {"ok": True}},
        }
        if not reason:
            response["expect_optional_body"] = {"reason": ""}
        return response

    c = case("support_refusal_and_discount", stage="support", patch={"hardship": True})
    c["utterance"] = "No, I do not want to be transferred. Can I get a discount?"
    c["evidence"] = "PUB-05, 106.540s, before first Snekbox error at 138.336s"
    step(c, turn("plan", "reject", turn_question="unknown"), operations=["close_call"])
    c["responses"] = [close("no_promise", "hardship")]

    c = case("support_acceptance_and_ai_question", stage="support", patch={"hardship": True})
    c["responses"] = [
        {
            "operation": "transfer_to_agent",
            "expect_body": {"queue": "support"},
            "result": {"data": {"ok": True}},
        },
        close("transferred", "support"),
    ]
    step(
        c,
        turn("question", "accept", turn_question="ai"),
        operations=["transfer_to_agent", "close_call"],
    )

    c = case(
        "support_refusal_without_secondary_question", stage="support", patch={"hardship": True}
    )
    step(c, turn("reject", "reject"), operations=["close_call"])
    c["responses"] = [close("no_promise", "hardship")]

    c = case(
        "question_is_not_payment_consent",
        stage="confirm",
        product_patch={"readback": "900.0|2026-10-14"},
    )
    step(c, turn("question", turn_question="ai"), operations=[])

    c = case(
        "hardship_transfer_refusal_preserves_voluntary_payment",
        stage="support",
        patch={"hardship": True},
        product_patch={"full_too_much": True},
    )
    step(
        c,
        turn("proposal", "reject", turn_amount="300", turn_date="2026-10-16"),
        stage="confirm",
        variables={
            "call_state.products.p1.amount": 300,
            "call_state.products.p1.date": "2026-10-16",
        },
        operations=[],
    )

    c = case("actual_restructuring_request_still_transfers")
    c["responses"] = [
        {
            "operation": "transfer_to_agent",
            "expect_body": {"queue": "restructuring"},
            "result": {"data": {"ok": True}},
        },
        close("transferred", "restructuring"),
    ]
    step(c, turn("plan", turn_plan="yes"), operations=["transfer_to_agent", "close_call"])

    c = case("accept_offer_requires_readback")
    step(c, turn("accept", "accept"), stage="confirm", operations=[])

    c = case(
        "changed_date_invalidates_confirmation",
        stage="confirm",
        product_patch={"readback": "900.0|2026-10-14"},
    )
    step(
        c,
        turn("accept", "accept", turn_date="2026-10-16"),
        stage="confirm",
        variables={
            "call_state.products.p1.date": "2026-10-16",
            "call_state.products.p1.confirmed": "",
        },
        operations=[],
    )

    c = case(
        "changed_amount_invalidates_confirmation",
        stage="confirm",
        product_patch={"readback": "900.0|2026-10-14", "full_too_much": True},
    )
    step(
        c,
        turn("accept", "accept", turn_amount="300"),
        stage="confirm",
        variables={"call_state.products.p1.amount": 300, "call_state.products.p1.confirmed": ""},
        operations=[],
    )

    c = case("exact_date_survives_ai_question")
    step(
        c,
        turn("proposal", turn_date="2026-10-16", turn_question="ai"),
        stage="confirm",
        variables={"call_state.products.p1.date": "2026-10-16"},
        operations=[],
    )

    c = case("holiday_moves_to_next_business_day")
    step(
        c,
        turn("proposal", turn_date="2026-10-26"),
        variables={"call_state.products.p1.date": "2026-10-27"},
        operations=[],
    )

    c = case("date_after_deadline_is_not_written")
    step(
        c,
        turn("proposal", turn_date="2026-11-03"),
        variables={"call_state.products.p1.date": "2026-11-02"},
        operations=[],
    )

    c = case("below_floor_is_not_written", product_patch={"full_too_much": True})
    step(
        c,
        turn("proposal", turn_amount="100"),
        variables={"call_state.products.p1.amount": 300},
        operations=[],
    )

    c = case(
        "recorded_promise_cannot_be_changed",
        stage="recorded",
        product_patch={
            "recorded": True,
            "confirmed": "900.0|2026-10-14",
            "readback": "900.0|2026-10-14",
            "create_attempts": 1,
        },
    )
    step(
        c,
        turn("change_recorded", turn_amount="300", turn_date="2026-10-16"),
        variables={
            "call_state.products.p1.amount": 900,
            "call_state.products.p1.date": "2026-10-14",
        },
        operations=[],
    )

    c = case(
        "successful_promise_then_single_final_close",
        stage="confirm",
        product_patch={"readback": "900.0|2026-10-14"},
    )
    c["responses"] = [
        {
            "operation": "create_promise",
            "expect_body": {"product_id": "p1", "amount": 900, "date": "2026-10-14"},
            "result": {"data": {"ok": True, "promise_id": "offline-promise"}},
        },
        close("promise", ""),
    ]
    step(c, turn("accept", "accept"), stage="recorded", operations=["create_promise"])
    step(c, turn("done"), operations=["create_promise", "close_call"])
    c["expect"] = {"node": "hang_up"}

    c = case(
        "two_failed_creates_never_claim_recorded",
        stage="confirm",
        product_patch={"readback": "900.0|2026-10-14"},
    )
    failure = {
        "operation": "create_promise",
        "result": {"data": {"ok": False, "error": "service_error"}},
    }
    c["responses"] = [deepcopy(failure), deepcopy(failure), close("no_promise", "service_error")]
    step(
        c,
        turn("accept", "accept"),
        variables={"call_state.products.p1.recorded": False},
        operations=["create_promise", "create_promise", "close_call"],
    )

    c = case("old_acceptance_does_not_confirm_next_turn")
    step(c, turn("accept", "accept"), stage="confirm", operations=[])
    ambiguous = turn("other")
    del ambiguous["turn_decision"]  # The next extraction may omit this field.
    step(c, ambiguous, operations=[])

    for verified in (True, False):
        c = case(
            f"insistent_human_request_verified_{verified}",
            stage="negotiate" if verified else "security",
            patch={
                "verified": verified,
                "dob_pass": verified,
                "security_field": "phone_last4" if verified else "date_of_birth",
            },
        )
        c["responses"] = [
            {
                "operation": "transfer_to_agent",
                "expect_body": {"queue": "general"},
                "result": {"data": {"ok": True}},
            },
            close("transferred", "human"),
        ]
        step(c, turn("human", turn_human="yes"), operations=[])
        step(
            c,
            turn("human", "reject", turn_human="yes"),
            operations=["transfer_to_agent", "close_call"],
        )

    for days_late, deadline, budget in [(5, "2026-11-02", 1), (28, "2026-10-23", 2)]:
        c = case(f"account_deadline_band_{days_late}", patch={"pending": "account"})
        c["start_node"] = "account"
        c["responses"] = [
            {
                "operation": "get_account",
                "result": {
                    "data": {
                        "ok": True,
                        "products": [
                            {
                                "product_id": "p1",
                                "type": "personal_loan",
                                "past_due": 900,
                                "days_late": days_late,
                                "instalment_amount": 300,
                            }
                        ],
                    }
                },
            }
        ]
        c["expect"] = {
            "node": "speak",
            "operations": ["get_account"],
            "variables": {
                "call_state.products.p1.deadline": deadline,
                "call_state.products.p1.budget": budget,
                "call_state.products.p1.floor": 300,
            },
        }

    c = case(
        "unverified_payment_offer_cannot_bypass_security",
        stage="security",
        patch={
            "verified": False,
            "dob_pass": False,
            "security_field": "date_of_birth",
        },
    )
    step(
        c,
        turn("proposal", turn_amount="900", turn_date="2026-10-16"),
        variables={"call_state.verified": False},
        operations=[],
    )
    return {
        "name": "Brief A Astra decision regressions",
        "contract": "Astra A speak/advance + call_state v1",
        "limitations": (
            "Scripted extractions and fake bank responses; no dialogue or official judge."
        ),
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream:
        json.dump(suite(), stream, indent=2)
        stream.write("\n")
    print(args.out)


if __name__ == "__main__":
    main()
