"""Answer the source-backed remainder question without changing negotiation state."""

from __future__ import annotations

import argparse
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
CALL = ROOT / ("diagnostics/v15-a012-live-read-001/practice/"
               "call-86fcb5dc-5dec-418f-8aed-6b1dd16c8727.json")
ANSWER = "Any unpaid part of the past-due amount stays overdue."


def code_field(workflow):
    return workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"][
        "properties"]["code"]


def repair(code):
    anchor = """    else:
     prefix = 'I do not have that information. '
   elif question == 'purpose':"""
    replacement = r"""    elif (re.search(r'\b(?:rest|remainder|remaining|unpaid)\b', raw_normal)
          and re.search(r'\b(?:overdue|past due|late)\b', raw_normal)):
     prefix = 'Any unpaid part of the past-due amount stays overdue. '
    else:
     prefix = 'I do not have that information. '
   elif question == 'purpose':"""
    assert code.count(anchor) == 1
    return code.replace(anchor, replacement, 1)


def seed():
    for message in json.loads(CALL.read_text())["call"]["messages"]:
        try:
            event = json.loads(message.get("message", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        values = event.get("variable_table", {})
        if (event.get("event") == "route_evaluated" and event.get("target") == "advance"
                and "rest still" in values.get("turn_raw", "")):
            return values
    raise ValueError("Native remainder-question input missing")


def probe(code, values):
    return execute_python(code, {"vars": copy.deepcopy(values)})["vars"]


def state_without_wording(result):
    result = copy.deepcopy(result)
    result.pop("instruction", None)
    result.pop("confirmation_text", None)
    state = json.loads(result.pop("call_state"))
    state.pop("prompt", None)
    state.pop("carry_prefix", None)
    return result, state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def action():
        source = args.parent / "workflow.json"
        workflow = json.loads(source.read_text())
        baseline = code_field(workflow)["default"]
        code_field(workflow)["default"] = repair(baseline)
        compiled, changes = compile_workflow(WorkflowBuild(
            workflow=workflow,
            rule_nodes=json.loads((args.parent / "rule-nodes.json").read_text())))
        code = code_field(compiled.workflow)["default"]
        write_json(args.out / "workflow.json", compiled.workflow)
        values = seed()
        rows = []
        positives = [values["turn_raw"], "16 October. Does the remaining balance stay overdue?",
                     "16 October. Will the unpaid amount still be past due?",
                     "16 October. Is the remainder still late?"]
        for raw in positives:
            v = {**values, "turn_raw": raw}
            old, new = probe(baseline, v), probe(code, v)
            state = json.loads(new["call_state"])
            p = state["products"]["p1"]
            expected_confirmation = old["confirmation_text"].replace(
                "I do not have that information.", ANSWER, 1)
            passed = (ANSWER in new["instruction"] and new["next"] == "confirm_speak"
                      and new["confirmation_text"] == expected_confirmation
                      and state_without_wording(old) == state_without_wording(new)
                      and state["stage"] == "confirm" and p["amount"] == 637.5
                      and p["date"] == "2026-10-16" and not p["confirmed"]
                      and not p["recorded"])
            rows.append({"id": raw, "passed": passed,
                         "baseline_instruction": old["instruction"],
                         "candidate_instruction": new["instruction"]})
        for name in ("unverified", "third_party", "unknown", "no_question",
                     "credit_score", "minimum", "deadline"):
            v = copy.deepcopy(values)
            state = json.loads(v["call_state"])
            if name == "unverified":
                state.update(verified=False, stage="security", security_field="date_of_birth")
            elif name == "third_party":
                state.update(caller_status="third_party", stage="relative_availability")
            elif name in ("unknown", "no_question"):
                v["turn_question"] = "unknown" if name == "unknown" else "none"
            elif name == "credit_score":
                v["turn_raw"] = "16 October. How will this affect my credit score?"
            elif name == "minimum":
                v["turn_raw"] = "16 October. What is the minimum payment?"
            else:
                v["turn_raw"] = "16 October. What is the latest date?"
            v["call_state"] = json.dumps(state)
            old, new = probe(baseline, v), probe(code, v)
            rows.append({"id": name, "passed": old == new and ANSWER not in new["instruction"]})
        report = {"passed": sum(r["passed"] for r in rows), "total": len(rows),
                  "rows": rows, "source": str(source), "source_call": str(CALL),
                  "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                  "workflow_sha256": hashlib.sha256(
                      (args.out / "workflow.json").read_bytes()).hexdigest(),
                  "compiler_changes": changes, "source_rule": "Brief A26 and A43",
                  "native_simulation": False, "fresh_generation": False, "promoted": False}
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "remaining_overdue_repair", action)
    print(json.dumps({"passed": report["passed"], "total": report["total"]}))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
