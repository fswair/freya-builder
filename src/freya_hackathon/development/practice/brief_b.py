"""Public Brief B development fixtures for the initialize/dialogue/decide state contract.

These are offline test scenarios, not policy or prompts used by the generic builder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from freya_hackathon.offline import Replay


def suite(workflow: dict) -> dict:
    initial = Replay(
        workflow,
        {
            "now": "2026-10-14T11:00",
            "title": "Mx",
            "last_name": "Example",
            "customer_id": "offline-b",
        },
        [],
    )
    initial.advance()
    state = json.loads(initial.variables["state"])
    state.update(verified=True, dob_passed=True, phone_passed=True, phase="security")
    initial.variables["state"] = json.dumps(state)
    # Authentication is deliberately preconditioned: these tests cover product handling.
    products = [
        {
            "product_id": "p1",
            "type": "credit_card",
            "card_last4": "1111",
            "past_due": 300,
            "days_late": 4,
            "minimum_payment": 90,
        },
        {
            "product_id": "p2",
            "type": "personal_loan",
            "past_due": 900,
            "days_late": 20,
            "instalment_amount": 300,
        },
    ]
    cases = []

    def bank(operation: str, body=None, *, ok=True, **data) -> dict:
        return {
            "operation": operation,
            "expect_body": body or {},
            "result": {"data": {"ok": ok, **data}},
        }

    def close(outcome: str, reason="") -> dict:
        result = bank("close_call", {"outcome": outcome, **({"reason": reason} if reason else {})})
        if not reason:
            result["expect_optional_body"] = {"reason": ""}
        return result

    def case(name: str) -> dict:
        c = {
            "id": name,
            "start_node": "get_account",
            "variables": deepcopy(initial.variables),
            "responses": [bank("get_account", products=products)],
            "turns": [],
            "expect": {"node": "end_call"},
        }
        cases.append(c)
        return c

    def turn(decision="none", **extra) -> dict:
        return {
            "turn_decision": decision,
            "turn_name": "none",
            "turn_topics": "",
            "turn_scope": "all",
            "turn_amount": "",
            "turn_date": "",
            "turn_date_kind": "none",
            "turn_date_words": "",
            "turn_time": "",
            "turn_answer": "",
            "turn_terms": "[]",
            "turn_question": "",
            **extra,
        }

    def step(c: dict, extracted: dict, *, operations: list[str], variables=None):
        c["turns"].append(
            {
                "extracted": extracted,
                "expect": {"operations": operations, "variables": variables or {}},
            }
        )

    def propose(c: dict, date="2026-10-16"):
        step(
            c,
            turn("reject", turn_date=date, turn_date_kind="exact"),
            operations=["get_account"],
            variables={"state.phase": "confirm"},
        )

    def promise(pid, amount, date, *, ok=True) -> dict:
        return bank(
            "create_promise",
            {"product_id": pid, "amount": amount, "date": date},
            ok=ok,
            **({"promise_id": "offline-" + pid} if ok else {"error": "service_error"}),
        )

    c = case("common_date_clamped_per_product")
    propose(c, "2026-10-30")
    c["turns"][-1]["expect"]["variables"].update(
        {
            "state.products.0.date": "2026-10-30",
            "state.products.1.date": "2026-10-23",
            "state.products.0.deadline": "2026-11-02",
            "state.products.1.deadline": "2026-10-23",
        }
    )
    c["responses"] += [
        promise("p1", 300, "2026-10-30"),
        promise("p2", 900, "2026-10-23"),
        close("promise"),
    ]
    step(
        c,
        turn("accept"),
        operations=["get_account", "create_promise", "create_promise"],
        variables={"state.products.0.status": "recorded", "state.products.1.status": "recorded"},
    )
    step(
        c,
        turn(turn_topics="done"),
        operations=["get_account", "create_promise", "create_promise", "close_call"],
    )

    for transfer in (False, True):
        c = case("partial_failure_then_transfer" if transfer else "partial_failure_keeps_success")
        propose(c)
        c["responses"] += [
            promise("p1", 300, "2026-10-16"),
            promise("p2", 900, "2026-10-16", ok=False),
            promise("p2", 900, "2026-10-16", ok=False),
        ]
        writes = ["get_account", "create_promise", "create_promise", "create_promise"]
        step(
            c,
            turn("accept"),
            operations=writes,
            variables={
                "state.products.0.status": "recorded",
                "state.products.1.reason": "service_error",
            },
        )
        if transfer:
            c["responses"] += [
                bank("transfer_to_agent", {"queue": "restructuring"}),
                close("transferred", "restructuring"),
            ]
            step(
                c,
                turn(turn_topics="restructuring"),
                operations=writes + ["transfer_to_agent", "close_call"],
                variables={"state.products.0.status": "recorded"},
            )
        else:
            c["responses"] += [
                bank("add_note", {"product_id": "p2", "reason": "service_error"}),
                close("promise"),
            ]
            step(c, turn(turn_topics="done"), operations=writes + ["add_note", "close_call"])

    c = case("pending_paid_note_dropped_on_transfer")
    c["responses"] += [
        bank("transfer_to_agent", {"queue": "restructuring"}),
        close("transferred", "restructuring"),
    ]
    step(
        c,
        turn(turn_topics="paid", turn_scope="p1"),
        operations=["get_account"],
        variables={"state.products.0.reason": "paid", "state.products.0.note_done": False},
    )
    step(
        c,
        turn(turn_topics="restructuring", turn_scope="p2"),
        operations=["get_account", "transfer_to_agent", "close_call"],
    )

    c = case("transfer_discards_unrecorded_agreements")
    propose(c)
    c["responses"] += [
        bank("transfer_to_agent", {"queue": "restructuring"}),
        close("transferred", "restructuring"),
    ]
    step(
        c,
        turn(turn_topics="restructuring"),
        operations=["get_account", "transfer_to_agent", "close_call"],
    )

    c = case("no_promise_uses_first_nonpaid_reason")
    c["responses"] += [
        bank("add_note", {"product_id": "p1", "reason": "paid"}),
        bank("add_note", {"product_id": "p2", "reason": "dispute"}),
        close("no_promise", "dispute"),
    ]
    step(c, turn(turn_topics="paid", turn_scope="p1"), operations=["get_account"])
    step(
        c,
        turn(turn_topics="dispute", turn_scope="p2"),
        operations=["get_account", "add_note", "add_note", "close_call"],
    )
    c = case("three_products_one_confirmation")
    c["responses"][0]["result"]["data"]["products"] = deepcopy(products) + [
        {
            "product_id": "p3",
            "type": "car_loan",
            "past_due": 75,
            "days_late": 3,
            "instalment_amount": 25,
        }
    ]
    propose(c)
    c["responses"] += [
        promise("p1", 300, "2026-10-16"),
        promise("p2", 900, "2026-10-16"),
        promise("p3", 75, "2026-10-16"),
        close("promise"),
    ]
    writes = ["get_account", "create_promise", "create_promise", "create_promise"]
    step(c, turn("accept"), operations=writes, variables={"state.products.2.status": "recorded"})
    step(c, turn(turn_topics="done"), operations=writes + ["close_call"])

    return {
        "name": "Brief B deterministic regressions",
        "cases": cases,
        "limitations": "Verified fixture; scripted extraction; no dialogue or official judge.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    data = args.workflow.read_bytes()
    result = suite(json.loads(data))
    result["fixture_workflow_sha256"] = hashlib.sha256(data).hexdigest()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(args.out)


if __name__ == "__main__":
    main()
