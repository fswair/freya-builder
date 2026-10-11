"""Ground contradictory affordability extraction and verify Brief A rule 30."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path

from repair_a_contextual_assent import code_field, native_input

from freya_hackathon.builder import write_json
from freya_hackathon.compiler import compile_workflow
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync
from freya_hackathon.workflow import WorkflowBuild

SOURCE = ("diagnostics/v13-a-pub06-call-read-001/practice/"
          "call-6cb807fd-01ee-4bf0-8052-7d55fb420ee6.json")


def repair(code):
    assert "elif explicit_nonanswer:\n     persuade(p)" in code
    anchor = "   financial_uncertain = False"
    assert code.count(anchor) == 1
    code = code.replace(anchor, '''   explicit_full_unaffordable = bool(
    stage in ('negotiate', 'confirm') and valid_candidate(p)
    and number(p['amount']) == number(p['past_due'])
    and not quoted_report and not raw_reporting and '?' not in raw_turn
    and re.fullmatch(r'(?:(?:the (?:full|whole|entire) (?:amount|balance)|that|it) '
                     r'(?:is|s) too (?:much|high|large|big)'
                     r'(?: (?:for me|right now|at the moment))*|'
                     r'i (?:cannot|can t|cant) (?:pay|afford|manage|cover) '
                     r'the (?:full|whole|entire) (?:amount|balance)'
                     r'(?: (?:right now|at the moment))?)', raw_normal))
   if explicit_full_unaffordable:
    full_unaffordable = True
   financial_uncertain = False''', 1)
    guard = "if full_unaffordable and (not re.search("
    assert code.count(guard) == 1
    code = code.replace(guard,
                        "if full_unaffordable and (not explicit_full_unaffordable) "
                        "and (not re.search(", 1)
    ast.parse(code)
    return code


def probe(code, values):
    result = execute_python(code, {"vars": copy.deepcopy(values)})["vars"]
    state = json.loads(result["call_state"])
    product = state["products"][state["product_id"]]
    return {"next": result["next"], "stage": state["stage"], "product": product,
            "instruction": result["instruction"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--baseline-workflow", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def action():
        path = args.parent / "workflow.json"
        workflow = json.loads(path.read_text())
        original_code = code_field(workflow)["default"]
        baseline = code_field(json.loads(args.baseline_workflow.read_text()))["default"] \
            if args.baseline_workflow else original_code
        if not args.probe_only:
            code_field(workflow)["default"] = repair(original_code)
        compiled, changes = compile_workflow(WorkflowBuild(
            workflow=workflow,
            rule_nodes=json.loads((args.parent / "rule-nodes.json").read_text())))
        write_json(args.out / "workflow.json", compiled.workflow)
        code = code_field(compiled.workflow)["default"]
        template = native_input(SOURCE, "The full amount is too much right now.")
        state = json.loads(template["call_state"])
        before = state["products"][state["product_id"]]
        rows = []
        for raw in ("The full amount is too much right now.",
                    "That is too much for me right now.",
                    "The entire balance is too high.",
                    "I cannot pay the full amount.",
                    "I can't afford the whole balance right now."):
            values = copy.deepcopy(template)
            values["turn_raw"] = raw
            old, new = probe(baseline, values), probe(code, values)
            product = new["product"]
            rows.append({"id": raw, "baseline_amount": old["product"]["amount"],
                "candidate_amount": product["amount"],
                "passed": product["amount"] == before["floor"]
                and product["date"] == before["date"]
                and product["attempts"] == before["attempts"]
                and product["floor_offered"] and not product["recorded"]})
        for raw in ('"The full amount is too much right now."',
                    "Is the full amount too much?", "The full amount is not too much.",
                    "He said the full amount is too much.",
                    "If the full amount is too much.",
                    "The full amount is too much; I can pay 800 on Friday."):
            values = copy.deepcopy(template)
            values["turn_raw"] = raw
            rows.append({"id": raw, "passed": probe(baseline, values) == probe(code, values)})

        # Rule 30 explicitly counts a non-answer. Missing extraction is different.
        for raw in ("You tell me.", "I don't know.", "Whatever.", "No idea."):
            values = copy.deepcopy(template)
            values.update(turn_raw=raw, turn_intent="other", turn_decision="none")
            new = probe(code, values)
            rows.append({"id": "rule30_" + raw,
                "passed": new["product"]["attempts"] == before["attempts"] + 1
                and not new["product"]["recorded"]})
        values = copy.deepcopy(template)
        values.update(turn_raw="", turn_intent="other", turn_decision="none",
                      turn_coverage="none")
        new = probe(code, values)
        rows.append({"id": "missing_extraction_is_not_nonanswer",
                     "passed": new["product"]["attempts"] == before["attempts"]})
        current = execute_python(code, {"vars": copy.deepcopy(template)})["vars"]
        stages = []
        for raw, decision in (("You tell me.", "none"),
                              ("I can do that.", "accept"), ("Yes.", "accept")):
            current.update(turn_raw=raw, turn_intent="other", turn_decision=decision,
                           turn_consent_words=raw if decision == "accept" else "",
                           turn_coverage="complete", turn_missing="none")
            current = execute_python(code, {"vars": current})["vars"]
            stages.append(current["next"])
        final_state = json.loads(current["call_state"])
        final_product = final_state["products"][final_state["product_id"]]
        rows.append({"id": "native_phrases_floor_nonanswer_accept_confirm",
            "routes": stages, "amount": final_product["amount"],
            "date": final_product["date"], "attempts": final_product["attempts"],
            "passed": stages == ["speak", "confirm_speak", "create"]
            and final_product["amount"] == before["floor"]
            and final_product["date"] == "2026-10-16"
            and final_product["attempts"] == 1})
        report = {"passed": sum(r["passed"] is True for r in rows), "total": len(rows),
            "rows": rows, "source": str(path), "source_call": SOURCE,
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "workflow_sha256": hashlib.sha256(
                (args.out / "workflow.json").read_bytes()).hexdigest(),
            "compiler_changes": changes,
            "rule_basis": "Brief A 26: offer floor for full-amount unaffordability; "
                          "Brief A 30: a flat refusal OR non-answer uses an attempt.",
            "supersedes": "The neutral-answer repair's preserve-attempt expectation "
                          "contradicted rule 30. Its historical report is retained, not endorsed.",
            "probe_only": args.probe_only,
            "baseline_workflow": str(args.baseline_workflow)
            if args.baseline_workflow else str(path),
            "fresh_generation": False, "native_simulation": False, "promoted": False}
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "a_affordability_and_rule30", action)
    print(json.dumps({"passed": report["passed"], "total": report["total"]}))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
