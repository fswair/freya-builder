"""Check Brief A's no-repeat rule while permitting a new deadline offer."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from repair_a_contextual_assent import NATIVE, code_field, native_input

from freya_hackathon.builder import write_json
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def check():
        codes = {
            name: code_field(json.loads(path.read_text()))["default"]
            for name, path in (("baseline", args.baseline), ("candidate", args.candidate))
        }
        template = native_input(*NATIVE[0])
        rows = []
        for same_offer in (True, False):
            values = copy.deepcopy(template)
            state = json.loads(values["call_state"])
            product = state["products"][state["product_id"]]
            product.update(
                attempts=1,
                floor_offered=True,
                full_too_much=True,
                confirmed="",
                readback="",
                refused_offers=[],
            )
            product["date"] = product["deadline"] if same_offer else "2026-10-16"
            state["stage"] = "negotiate"
            values["call_state"] = json.dumps(state)
            values.update(
                turn_raw="No.",
                turn_intent="reject",
                turn_decision="reject",
                turn_consent_words="",
                turn_coverage="complete",
                turn_missing="none",
            )
            results = {}
            for name, code in codes.items():
                result = execute_python(code, {"vars": copy.deepcopy(values)})["vars"]
                final = json.loads(result["call_state"])
                actual = final["products"][final["product_id"]]
                results[name] = {
                    "route": result["next"],
                    "outcome": result.get("outcome"),
                    "reason": result.get("reason"),
                    "terminal": final["terminal_selected"],
                    "amount": actual["amount"],
                    "date": actual["date"],
                    "attempts": actual["attempts"],
                }
            candidate = results["candidate"]
            if same_offer:
                passed = (
                    candidate["route"] == "goodbye"
                    and candidate["terminal"]
                    and candidate["outcome"] == "no_promise"
                    and candidate["reason"] == "refused"
                )
            else:
                passed = (
                    candidate["route"] == "speak"
                    and not candidate["terminal"]
                    and candidate["amount"] == product["floor"]
                    and candidate["date"] == product["deadline"]
                    and candidate["attempts"] == 2
                )
            rows.append(
                {
                    "id": "same_rejected_pair" if same_offer else "new_deadline_offer",
                    "passed": passed,
                    "results": results,
                }
            )
        report = {
            "passed": sum(r["passed"] is True for r in rows),
            "total": len(rows),
            "rows": rows,
            "candidate": str(args.candidate),
            "candidate_sha256": hashlib.sha256(args.candidate.read_bytes()).hexdigest(),
            "source_rules": [
                "Brief A 26 permits floor again on deadline",
                "Brief A 31 prohibits repeating an already refused offer",
            ],
            "network_calls": False,
            "official_score": False,
        }
        write_json(args.out / "report.json", report)
        return report

    result = capture_sync(args.out, "a_no_repeat_refused_offer", check)
    print(json.dumps({"passed": result["passed"], "total": result["total"]}))
    if result["passed"] != result["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
