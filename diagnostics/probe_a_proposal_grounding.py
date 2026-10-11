"""Compare clear amount/date proposals and misleading numerical mentions using a train state."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync


def examples() -> list[dict]:
    positive = [
        ("600 on Friday.", "600", "no"),
        ("Six hundred on Friday.", "600", "no"),
        ("600 on Friday is what I had in mind.", "600", "no"),
        (
            "Six hundred on Friday is what I had in mind; I cannot manage the whole amount.",
            "600",
            "yes",
        ),
        ("600 on Friday is what I have in mind; I cannot cover the full amount.", "600", "yes"),
        ("Six hundred on Friday would work for me.", "600", "no"),
        ("600 is what I had in mind for Friday.", "600", "no"),
        ("600 on Friday is my proposal.", "600", "no"),
        ("I can pay 600 on Friday.", "600", "no"),
        ("850 on Friday.", "850", "no"),
        ("Eight hundred fifty on Friday works for me.", "850", "no"),
        ("850 by Friday is our offer.", "850", "no"),
        ("850 on Friday will work for us.", "850", "no"),
        ("Eight hundred fifty on Friday is what we had in mind.", "850", "no"),
    ]
    negative = [
        "If I said 850 on Friday, would that work for me?",
        "You said 850 on Friday would work for me.",
        "My friend says 850 on Friday would work for me.",
        'She said "850 on Friday is what I had in mind."',
        "Would 850 on Friday work for me?",
        '"850 on Friday is my proposal," is what she said.',
        "Card ending 0850 on Friday is what I had in mind.",
        "850 on Friday used to work for me.",
    ]
    return [
        {
            "id": f"positive-{i + 1:02}",
            "raw": raw,
            "amount": amount,
            "unaffordable": unable,
            "positive": True,
        }
        for i, (raw, amount, unable) in enumerate(positive)
    ] + [
        {
            "id": f"negative-{i + 1:02}",
            "raw": raw,
            "amount": "850",
            "unaffordable": "no",
            "positive": False,
        }
        for i, raw in enumerate(negative)
    ]


def state_from_call(path: Path) -> dict:
    response = json.loads(path.read_text())
    call = response.get("call", response)
    values = None
    for message in call["messages"]:
        if message.get("role") != "system":
            continue
        try:
            event = json.loads(message["message"])
        except (ValueError, TypeError):
            continue
        table = event.get("variable_table")
        if isinstance(table, dict):
            values = {
                key: value.get("value") if isinstance(value, dict) and "value" in value else value
                for key, value in table.items()
            }
    if not values or not values.get("call_state"):
        raise ValueError("No recorded A controller state")
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path, nargs="+")
    parser.add_argument("--call", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    base = state_from_call(args.call)

    def probe() -> dict:
        results = []
        for path in args.workflow:
            workflow = json.loads(path.read_text())
            code = workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"][
                "properties"
            ]["code"]["default"]
            rows = []
            for case in examples():
                values = copy.deepcopy(base)
                for field in workflow["nodes"]["speak"]["extraction_schema"]:
                    options = field.get("enum_values", [])
                    values[field["name"]] = next(
                        (item for item in ("none", "no", "unknown", "other") if item in options), ""
                    )
                values.update(
                    turn_raw=case["raw"],
                    turn_amount=case["amount"],
                    turn_date="Friday",
                    turn_intent="proposal",
                    turn_coverage="complete",
                    turn_missing="none",
                    turn_full_unaffordable=case["unaffordable"],
                )
                after = execute_python(code, {"vars": values})["vars"]
                state = json.loads(after["call_state"])
                product = state["products"]["p1"]
                expected = max(float(case["amount"]), float(product["floor"]))
                no_write = after.get("next") != "create" and product.get("recorded") is not True
                grounded = (
                    product["amount"] == expected and product["date"] == "2026-10-16"
                    if case["positive"]
                    else product["amount"] != float(case["amount"])
                )
                rows.append(
                    {
                        **case,
                        "passed": no_write and grounded,
                        "next": after.get("next"),
                        "amount": product["amount"],
                        "date": product["date"],
                        "instruction": after.get("instruction"),
                        "no_write": no_write,
                    }
                )
            results.append(
                {
                    "workflow": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "passed": sum(row["passed"] is True for row in rows),
                    "total": len(rows),
                    "rows": rows,
                }
            )
        report = {
            "scope": "Synthetic local train-state probes; no native score or network request.",
            "negative_scope": (
                "Negative cases deliberately supply a misleading extracted amount; raw evidence "
                "must not turn quoted, hypothetical, third-person or card numbers into a proposal."
            ),
            "call_source": str(args.call),
            "call_sha256": hashlib.sha256(args.call.read_bytes()).hexdigest(),
            "results": results,
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "offline_a_proposal_grounding", probe)
    print(
        json.dumps(
            [{k: row[k] for k in ("workflow", "passed", "total")} for row in report["results"]]
        )
    )


if __name__ == "__main__":
    main()
