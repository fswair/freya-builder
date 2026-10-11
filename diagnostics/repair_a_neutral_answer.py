"""Keep an ambiguous reply from consuming a payment persuasion attempt."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.compiler import compile_workflow
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync
from freya_hackathon.workflow import WorkflowBuild

ROOT = Path(__file__).resolve().parents[1]
CALL = ROOT / (
    "diagnostics/v13-a-pub06-call-read-001/practice/call-6cb807fd-01ee-4bf0-8052-7d55fb420ee6.json"
)


def code_field(workflow):
    return workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]


def repair(code):
    anchor = "elif explicit_nonanswer:\n     persuade(p)"
    assert code.count(anchor) == 1
    code = code.replace(
        anchor,
        "elif explicit_nonanswer:\n"
        "     tell(stage, 'Ask neutrally whether the customer accepts or declines "
        "the existing offer. Their reply is not refusal. Keep the pending amount, "
        "date and offer history unchanged; do not repeat a persuasion pitch, "
        "advance the offer or record a promise.')",
        1,
    )
    anchor = "financial_uncertain = False"
    assert code.count(anchor) == 1
    code = code.replace(
        anchor,
        "if explicit_nonanswer:\n    decision = 'none'\n   " + anchor,
        1,
    )
    ast.parse(code)
    return code


def native_input():
    for message in json.loads(CALL.read_text())["call"]["messages"]:
        try:
            event = json.loads(message["message"])
        except (ValueError, TypeError):
            continue
        values = event.get("variable_table", {})
        if (
            event.get("event") == "route_evaluated"
            and message.get("nodeId") == "speak"
            and values.get("turn_raw") == "You tell me."
        ):
            return values
    raise ValueError("Native public-case nonanswer input not found")


def probe(code, values):
    result = execute_python(code, {"vars": copy.deepcopy(values)})["vars"]
    state = json.loads(result["call_state"])
    product = state["products"][state["product_id"]]
    return {
        "next": result["next"],
        "product": product,
        "instruction": result["instruction"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def action():
        source = args.parent / "workflow.json"
        workflow = json.loads(source.read_text())
        original_code = code_field(workflow)["default"]
        code_field(workflow)["default"] = repair(original_code)
        compiled, changes = compile_workflow(
            WorkflowBuild(
                workflow=workflow,
                rule_nodes=json.loads((args.parent / "rule-nodes.json").read_text()),
            )
        )
        write_json(args.out / "workflow.json", compiled.workflow)
        candidate_code = code_field(compiled.workflow)["default"]
        template = native_input()
        state = json.loads(template["call_state"])
        before = state["products"][state["product_id"]]
        fields = ("amount", "date", "attempts", "refused_offers", "confirmed", "readback")
        rows = []
        for raw in (
            "You tell me.",
            "I don't know.",
            "I do not know.",
            "Whatever.",
            "No idea.",
            "I have no idea.",
        ):
            for decision in ("none", "reject"):
                values = copy.deepcopy(template)
                values.update(turn_raw=raw, turn_decision=decision)
                candidate = probe(candidate_code, values)
                baseline = probe(original_code, values)
                passed = candidate["next"] == "speak" and all(
                    before[key] == candidate["product"][key] for key in fields
                )
                rows.append(
                    {
                        "raw": raw,
                        "decision": decision,
                        "passed": passed,
                        "baseline_date": baseline["product"]["date"],
                        "candidate_date": candidate["product"]["date"],
                        "baseline_attempts": baseline["product"]["attempts"],
                        "candidate_attempts": candidate["product"]["attempts"],
                    }
                )
        values = copy.deepcopy(template)
        values.update(turn_raw="No.", turn_decision="reject")
        baseline, candidate = probe(original_code, values), probe(candidate_code, values)
        rows.append(
            {
                "raw": values["turn_raw"],
                "baseline_attempts": baseline["product"]["attempts"],
                "candidate_attempts": candidate["product"]["attempts"],
                "passed": baseline == candidate
                and candidate["product"]["attempts"] > before["attempts"],
            }
        )
        report = {
            "passed": sum(row["passed"] is True for row in rows),
            "total": len(rows),
            "rows": rows,
            "source_workflow": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "workflow_sha256": hashlib.sha256(
                (args.out / "workflow.json").read_bytes()
            ).hexdigest(),
            "source_call": str(CALL.relative_to(ROOT)),
            "call_sha256": hashlib.sha256(CALL.read_bytes()).hexdigest(),
            "native_replay_case": "PUB-06 / You tell me. / decision=none",
            "other_cases": "synthetic paraphrases and genuine refusal control",
            "compiler_changes": changes,
            "native_simulation": False,
            "fresh_generation": False,
            "promoted": False,
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "a_neutral_answer_repair", action)
    print(json.dumps({"passed": report["passed"], "total": report["total"]}))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
