"""Check correction grounding and non-consent boundaries without network calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("complete_restatement", "No, I meant the full 420.00 today, 14 October.", "420", True),
    ("corrected_amount", "No, I meant 410 today, 14 October.", "410", True),
    ("apologetic_correction", "Sorry, I meant 410 today, 14 October.", "410", True),
    (
        "spoken_full_amount",
        "No, I meant four hundred and twenty today, 14 October.",
        "420",
        True,
    ),
    (
        "spoken_corrected_amount",
        "Sorry, I meant four hundred and ten today, 14 October.",
        "410",
        True,
    ),
    ("negated", "No, I did not mean 420 today.", "420", False),
    ("hypothetical", "If I meant 420 today, would that work?", "420", False),
    ("quoted", '"I meant 420 today" is not my agreement.', "420", False),
    ("third_person", "The agent said I meant 420 today.", "420", False),
    ("ungrounded_amount", "No, I meant 410 today, 14 October.", "420", False),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    workflow = json.loads(args.workflow.read_text())
    code = workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]
    snapshot = json.loads(
        (ROOT / "diagnostics/a-complete-restatement-002/training-snapshot.json").read_text()
    )

    def probe() -> dict:
        rows = []
        for name, raw, amount, accepted in CASES:
            values = {
                **snapshot,
                "turn_raw": raw,
                "turn_amount": amount,
                "turn_amount_reference": "none",
                "turn_date": "today",
                "turn_intent": "accept",
                "turn_decision": "accept",
                "turn_consent_words": "",
                "turn_question": "none",
                "turn_coverage": "complete",
                "turn_missing": "none",
            }
            result = execute_python(code, {"vars": values})["vars"]
            state = json.loads(result["call_state"])
            product = state["products"][state["product_id"]]
            readback = result["next"] == "confirm_speak"
            safe = result["next"] != "create" and not product.get("confirmed")
            rows.append(
                {
                    "case": name,
                    "raw": raw,
                    "extracted_amount": amount,
                    "expected_new_readback": accepted,
                    "next": result["next"],
                    "amount": product["amount"],
                    "instruction": result["instruction"],
                    "pass": safe
                    and readback == accepted
                    and (not accepted or product["amount"] == float(amount)),
                }
            )
        report = {
            "scope": "Synthetic advisory probes; no validation/holdout or native scores.",
            "workflow": str(args.workflow),
            "passed": sum(row["pass"] is True for row in rows),
            "total": len(rows),
            "cases": rows,
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "offline_restatement_safety", probe)
    print(json.dumps({k: report[k] for k in ("passed", "total")}))


if __name__ == "__main__":
    main()
