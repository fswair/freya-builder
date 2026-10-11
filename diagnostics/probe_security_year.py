"""Replay security-year grounding against saved A/B native security states."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync

ROOT = Path(__file__).resolve().parents[1]
CALLS = {
    "A": ROOT
    / (
        "diagnostics/v13-pub03-call-read-001/practice/"
        "call-7d46ae79-1b91-421f-b3d6-6b6110ca7321.json"
    ),
    "B": ROOT
    / (
        "diagnostics/v13-b-control-call-001/practice/call-543e8823-4d91-4965-8c24-e754d95cc5d7.json"
    ),
}
CASES = (
    ("It is 21 July 199", "1992-07-21", False),
    ("21/07/199", "1992-07-21", False),
    ("21 July 1990", "1992-07-21", False),
    ("21 July 1990", "1990-07-21", True),
    ("21/07/1990", "1990-07-21", True),
    ("Twenty first of July nineteen ninety two.", "1992-07-21", True),
    ("21 July 1992. I can pay 100 later.", "1992-07-21", True),
)


def seed(brief):
    state_key, node = ("call_state", "speak") if brief == "A" else ("state", "dialogue")
    for message in json.loads(CALLS[brief].read_text())["call"]["messages"]:
        try:
            event = json.loads(message["message"])
        except (ValueError, TypeError):
            continue
        values = event.get("variable_table", {})
        if event.get("event") != "route_evaluated" or message.get("nodeId") != node:
            continue
        state = json.loads(values.get(state_key, "{}"))
        if state.get("security_field") != "date_of_birth" or not values.get("turn_answer"):
            continue
        if brief == "A" and values.get("turn_raw") != "It is 21 July 199":
            continue
        return values
    raise ValueError(f"Native security input not found for {brief}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def evaluate():
        workflow = json.loads(args.workflow.read_text())
        brief = "B" if "decide" in workflow["nodes"] else "A"
        controller, state_key, route_key, verify = (
            ("decide", "state", "route", "verify_identity")
            if brief == "B"
            else ("advance", "call_state", "next", "verify")
        )
        code = workflow["nodes"][controller]["pre_actions"][0]["function"]["parameters"][
            "properties"
        ]["code"]["default"]
        template = seed(brief)
        initial_wrong = json.loads(template[state_key])["wrong"]
        rows = []
        for speech, answer, may_verify in CASES:
            values = copy.deepcopy(template)
            values.update(turn_raw=speech, turn_answer=answer)
            result = execute_python(code, {"vars": values})["vars"]
            state = json.loads(result[state_key])
            actual_verify = result[route_key] == verify
            passed = (
                actual_verify == may_verify
                and state["wrong"] == initial_wrong
                and state.get("verified") is not True
            )
            if may_verify:
                key = "request_answer" if brief == "B" else "security_answer"
                passed = passed and result[key] == answer
            rows.append(
                {
                    "speech": speech,
                    "extracted_answer": answer,
                    "expected_verify": may_verify,
                    "actual_verify": actual_verify,
                    "wrong_count": state["wrong"],
                    "passed": passed,
                }
            )
        report = {
            "brief": brief,
            "passed": sum(row["passed"] is True for row in rows),
            "total": len(rows),
            "rows": rows,
            "call": str(CALLS[brief].relative_to(ROOT)),
            "call_sha256": hashlib.sha256(CALLS[brief].read_bytes()).hexdigest(),
            "workflow_sha256": hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            "scope": "One exact native A input plus controlled A/B security-state variants",
            "native_simulation": False,
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "security_year_replay", evaluate)
    print(json.dumps({"passed": report["passed"], "total": report["total"]}))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
