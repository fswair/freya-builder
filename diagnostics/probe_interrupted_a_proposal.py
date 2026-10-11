"""Counterfactually resume A's controller from recorded, partially extracted training input."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("call", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--complete-extraction", action="store_true")
    parser.add_argument("--full-unaffordable", choices=("yes", "no"), default="no")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def probe() -> dict:
        workflow = json.loads(args.workflow.read_text())
        response = json.loads(args.call.read_text())
        call = response.get("call", response)
        values = {}
        extracted = []
        snapshot_index = None
        speech = []
        completed_functions = []
        for index, message in enumerate(call["messages"]):
            if message.get("role") in {"assistant", "user"}:
                speech.append({"role": message["role"], "text": message["message"], "index": index})
                continue
            if message.get("role") != "system":
                continue
            try:
                event = json.loads(message["message"])
            except (ValueError, TypeError):
                continue
            table = event.get("variable_table")
            if isinstance(table, dict):
                values = {
                    key: value.get("value")
                    if isinstance(value, dict) and "value" in value
                    else value
                    for key, value in table.items()
                }
                snapshot_index = index
                extracted = []
            if event.get("event") == "variable_extracted" and event.get("name"):
                values[event["name"]] = event.get("value")
                extracted.append(event["name"])
            if event.get("event") == "function_call_complete":
                result = event.get("result", {})
                completed_functions.append(
                    {"function": event.get("function"), "status": result.get("status")}
                )
        if snapshot_index is None or not values.get("call_state"):
            raise ValueError("No recorded A controller state")
        recorded_turn = {key: value for key, value in values.items() if key.startswith("turn_")}
        if args.complete_extraction:
            # Synthetic diagnostic only: complete the unobserved extraction for this proposal.
            for field in workflow["nodes"]["speak"]["extraction_schema"]:
                options = field.get("enum_values", [])
                values[field["name"]] = next(
                    (item for item in ("none", "no", "unknown", "other") if item in options), ""
                )
            values.update(
                turn_raw=recorded_turn.get("turn_raw", ""),
                turn_amount=recorded_turn.get("turn_amount", ""),
                turn_date=recorded_turn.get("turn_date", ""),
                turn_intent="proposal",
                turn_coverage="complete",
                turn_missing="none",
                turn_full_unaffordable=args.full_unaffordable,
            )
        code = workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"][
            "properties"
        ]["code"]["default"]
        after = execute_python(code, {"vars": values})["vars"]
        state = json.loads(after["call_state"])
        products = state.get("products", {})
        products = list(products.values()) if isinstance(products, dict) else products
        report = {
            "scope": "Counterfactual local controller execution, not the remote result.",
            "limits": (
                "Only recorded extraction events after the last state snapshot are applied. "
                "Unfinished extraction could produce additional values. No native score override."
            ),
            "workflow_sha256": hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            "call_sha256": hashlib.sha256(args.call.read_bytes()).hexdigest(),
            "ended_reason": call.get("endedReason"),
            "official_verdict": call.get("successCriteriaResult", {}).get("success"),
            "snapshot_index": snapshot_index,
            "recorded_extracted_fields": extracted,
            "recorded_turn": {
                key: recorded_turn.get(key)
                for key in (
                    "turn_raw",
                    "turn_amount",
                    "turn_date",
                    "turn_intent",
                    "turn_decision",
                    "turn_caller",
                    "turn_reason",
                    "turn_affordability",
                )
            },
            "input_mode": "synthetic-complete" if args.complete_extraction else "recorded-partial",
            "controller_input_turn": {
                key: value for key, value in values.items() if key.startswith("turn_")
            },
            "last_speech": speech[-2:],
            "completed_functions": completed_functions,
            "local_next": after.get("next"),
            "local_state_phase": state.get("phase"),
            "local_state_stage": state.get("stage"),
            "local_products": [
                {
                    key: product[key]
                    for key in (
                        "product_id",
                        "status",
                        "amount",
                        "date",
                        "offer_amount",
                        "offer_date",
                        "offer_kind",
                        "attempts",
                        "floor",
                        "minimum_payment",
                        "recorded",
                    )
                    if key in product
                }
                for product in products
            ],
            "local_speech_fields": {
                key: value
                for key, value in after.items()
                if key
                in {
                    "say",
                    "speech",
                    "prompt",
                    "message",
                    "next_text",
                    "closing_message",
                    "instruction",
                    "confirmation_text",
                }
            },
        }
        write_json(args.out / "report.json", report)
        return report

    result = capture_sync(args.out, "offline_interrupted_a_proposal", probe)
    print(json.dumps({key: result[key] for key in ("local_next", "local_products")}))


if __name__ == "__main__":
    main()
