"""Bind complete deictic assent to a valid, unresolved-free pending offer."""

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
NATIVE = (
    ("diagnostics/v13-a-pub06-call-read-001/practice/"
     "call-6cb807fd-01ee-4bf0-8052-7d55fb420ee6.json", "I can do that."),
    ("diagnostics/v13-pub03-call-read-001/practice/"
     "call-7d46ae79-1b91-421f-b3d6-6b6110ca7321.json", "Yes, I can pay that."),
)


def code_field(workflow):
    return workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"][
        "properties"
    ]["code"]


def repair(code):
    anchor = "   financial_uncertain = False"
    assert code.count(anchor) == 1
    replacement = '''   contextual_assent = bool(
    stage in ('negotiate', 'confirm') and valid_candidate(p)
    and not p.get('need_exact') and not p.get('unresolved_slot')
    and not s.get('human_pending') and question == 'none'
    and decision in ('none', 'accept') and not quoted_report
    and not withdrawal_current and '?' not in raw_turn
    and re.fullmatch(r'(?:(?:yes|yeah|sure|okay|ok) )?i '
                     r'(?:can|will) (?:do|pay|manage) that', raw_normal))
   if contextual_assent:
    # These complete replies reference the existing offer. Any extracted numbers
    # or dates are inferred context, not a new customer proposal.
    amount, day, amount_reference = ('', '', 'none')
    full_unaffordable = False
    coverage, missing_slot = ('complete', 'none')
    decision, consent_current, bare_yes = ('accept', True, True)
    evidence = raw_turn
   financial_uncertain = False'''
    code = code.replace(anchor, replacement, 1)
    ast.parse(code)
    return code


def native_input(path, raw):
    for message in json.loads((ROOT / path).read_text())["call"]["messages"]:
        try:
            event = json.loads(message["message"])
        except (ValueError, TypeError):
            continue
        values = event.get("variable_table", {})
        if (event.get("event") == "route_evaluated" and message.get("nodeId") == "speak"
                and values.get("turn_raw") == raw):
            return values
    raise ValueError(f"Missing native input: {raw}")


def probe(code, values):
    result = execute_python(code, {"vars": copy.deepcopy(values)})["vars"]
    state = json.loads(result["call_state"])
    return {"next": result["next"], "state": state, "instruction": result["instruction"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def action():
        workflow = json.loads(args.workflow.read_text())
        baseline_code = code_field(workflow)["default"]
        code_field(workflow)["default"] = repair(baseline_code)
        compiled, changes = compile_workflow(WorkflowBuild(
            workflow=workflow, rule_nodes=json.loads(args.rules.read_text())))
        write_json(args.out / "workflow.json", compiled.workflow)
        code = code_field(compiled.workflow)["default"]
        rows = []
        for path, raw in NATIVE:
            values = native_input(path, raw)
            baseline, candidate = probe(baseline_code, values), probe(code, values)
            before = json.loads(values["call_state"])
            old = before["products"][before["product_id"]]
            new = candidate["state"]["products"][before["product_id"]]
            rows.append({"id": raw, "source": path,
                "baseline_instruction": baseline["instruction"],
                "candidate_instruction": candidate["instruction"],
                "passed": candidate["next"] == "confirm_speak"
                and candidate["state"]["stage"] == "confirm"
                and all(old[k] == new[k] for k in ("amount", "date", "attempts"))
                and bool(new["readback"]) and not new["confirmed"]})

        fixture = json.loads((ROOT / "tests/fixtures/a-spoken-cents-turn.json").read_text())
        for raw, positive in (
            ("I can do that.", True), ("Yes, I can pay that.", True),
            ("Okay, I will do that.", True), ("I can manage that.", True),
            ("I can do that?", False), ('"I can do that."', False),
            ("If I can do that.", False), ("He says I can do that.", False),
            ("USER: I can do that.", False), ("I cannot do that.", False),
            ("I can do that but do not record it.", False),
            ("I can do that, but change the amount to 20.", False),
        ):
            values = copy.deepcopy(fixture)
            values.update({key: "" for key in values if key.startswith("turn_")})
            values.update(turn_raw=raw, turn_consent_words=raw, turn_decision="accept",
                          turn_intent="accept", turn_coverage="complete",
                          turn_missing="none", turn_amount_reference="none",
                          turn_question="none")
            baseline, candidate = probe(baseline_code, values), probe(code, values)
            rows.append({"id": raw, "positive": positive, "next": candidate["next"],
                "passed": (candidate["next"] == "create") if positive else
                (candidate == baseline and candidate["next"] != "create")})

        for key, value in (("need_exact", True), ("terms_unresolved", True),
                           ("unresolved_slot", "amount"), ("recorded", True)):
            values = native_input(*NATIVE[0])
            state = json.loads(values["call_state"])
            state["products"][state["product_id"]][key] = value
            values["call_state"] = json.dumps(state)
            rows.append({"id": "pending_" + key,
                         "passed": probe(baseline_code, values) == probe(code, values)})
        report = {"passed": sum(r["passed"] is True for r in rows),
            "total": len(rows), "rows": rows,
            "source": str(args.workflow),
            "source_sha256": hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            "workflow_sha256": hashlib.sha256(
                (args.out / "workflow.json").read_bytes()).hexdigest(),
            "compiler_changes": changes, "native_simulation": False,
            "fresh_generation": False, "promoted": False}
        write_json(args.out / "report.json", report)
        return report

    result = capture_sync(args.out, "a_contextual_assent_repair", action)
    print(json.dumps({"passed": result["passed"], "total": result["total"]}))
    if result["passed"] != result["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
