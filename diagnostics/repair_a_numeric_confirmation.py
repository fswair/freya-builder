"""Ground an inferred full/floor label in an explicit unchanged numeric readback."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from repair_a_remaining_overdue import code_field, probe

from freya_hackathon.builder import write_json
from freya_hackathon.compiler import compile_workflow
from freya_hackathon.recording import capture_sync
from freya_hackathon.workflow import WorkflowBuild

CALL = Path("diagnostics/v15-a001-live-read-001/practice/"
            "call-6440b7d7-42a7-401d-a52a-42ea61797cbe.json")


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
                and values.get("turn_raw") == "Yes, I confirm the payment of 420."):
            return values
    raise ValueError("Native numeric confirmation input missing")


def repair(code):
    anchor = """     if not any((re.search(pattern, normalized(x)) for x in positive_clauses)):
      amount_reference = 'none'
      if not amount or amount == 'conflict':"""
    replacement = anchor.splitlines()[0] + r"""
      reference_value = p['past_due'] if amount_reference == 'full' else p['floor']
      numeric_confirmation = re.fullmatch(
       r'(?:(?:yes|yeah|okay|ok)[,\s]+)?i (?:do )?confirm '
       r'(?:(?:the|my) )?(?:payment|amount)(?: of)? '
       r'([$£€]?\s*\d[\d,]*(?:\.\d{1,2})?'
       r'(?:\s+(?:dollars?|pounds?|euros?))?)\s*[.!]?', raw_turn, re.I)
      if (numeric_confirmation and stage == 'confirm' and consent_current
          and coverage == 'complete' and missing_slot == 'none'
          and question == 'none' and not financial_uncertain and not amount
          and not p.get('terms_unresolved') and not p.get('need_exact')
          and not p.get('unresolved_slot') and valid_candidate(p)
          and number(reference_value) == number(p['amount'])
          and p.get('readback') == str(p['amount']) + '|' + p['date']
          and amount_values(numeric_confirmation.group(1)) == {number(reference_value)}):
       amount = str(reference_value)
      amount_reference = 'none'
      if not amount or amount == 'conflict':"""
    assert code.count(anchor) == 1
    return code.replace(anchor, replacement, 1)


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
        write_json(args.out / "rule-nodes.json", compiled.rule_nodes)
        template = seed()
        rows = []
        positives = [template["turn_raw"], "I confirm the payment of 420",
                     "Yes, I confirm my payment of 420.00!",
                     "I do confirm the amount of $420.",
                     "Okay, I confirm the payment of 420 dollars."]
        for raw in positives:
            values = {**template, "turn_raw": raw, "turn_consent_words": raw}
            old, new = probe(baseline, values), probe(code, values)
            state = json.loads(new["call_state"])
            product = state["products"]["p1"]
            passed = (new["next"] == "create" and old["next"] != "create"
                      and product["amount"] == 420 and product["date"] == "2026-10-14"
                      and product["confirmed"] == "420.0|2026-10-14"
                      and not product["recorded"])
            rows.append({"id": raw, "passed": passed,
                         "baseline_next": old["next"], "candidate_next": new["next"],
                         "candidate_product": product})
        for name in ("different_amount", "explicit_new_date", "question", "quotation",
                     "reported", "withdrawal", "incomplete", "no_readback", "stale_readback",
                     "unresolved", "negotiate", "unverified", "third_party", "date_only"):
            values = copy.deepcopy(template)
            state = json.loads(values["call_state"])
            product = state["products"]["p1"]
            if name == "different_amount":
                values["turn_raw"] = "Yes, I confirm the payment of 421."
            elif name == "explicit_new_date":
                values["turn_raw"] += " Tomorrow."
            elif name == "question":
                values["turn_raw"] = "I confirm the payment of 420?"
            elif name == "quotation":
                values["turn_raw"] = '"I confirm the payment of 420."'
            elif name == "reported":
                values["turn_raw"] = "He said I confirm the payment of 420."
            elif name == "withdrawal":
                values["turn_raw"] += " Do not record this."
            elif name == "date_only":
                values["turn_raw"] = "I confirm October 14."
            elif name == "incomplete":
                values["turn_coverage"] = "incomplete"
            elif name == "no_readback":
                product["readback"] = ""
            elif name == "stale_readback":
                product["readback"] = "420.0|2026-10-15"
            elif name == "unresolved":
                product.update(terms_unresolved=True, unresolved_slot="date")
            elif name == "negotiate":
                state["stage"] = "negotiate"
            elif name == "unverified":
                state.update(verified=False, stage="security", security_field="date_of_birth")
            elif name == "third_party":
                state.update(caller_status="third_party", stage="relative_availability")
            values["turn_consent_words"] = values["turn_raw"]
            values["call_state"] = json.dumps(state)
            old, new = probe(baseline, values), probe(code, values)
            rows.append({"id": name, "passed": old == new and new["next"] != "create",
                         "candidate_next": new["next"]})
        for reference, amount in (("floor", 126.0), ("full", 1260.5)):
            values = copy.deepcopy(template)
            state = json.loads(values["call_state"])
            product = state["products"]["p1"]
            product.update(amount=amount, readback=f"{amount}|2026-10-14")
            if reference == "full":
                product["past_due"] = amount
            values.update(turn_raw=f"I confirm the amount of {amount}.",
                          turn_amount_reference=reference, turn_consent_words="")
            values["call_state"] = json.dumps(state)
            new = probe(code, values)
            after = json.loads(new["call_state"])["products"]["p1"]
            rows.append({"id": f"generic_{reference}_{amount}",
                         "passed": new["next"] == "create" and after["amount"] == amount
                         and after["confirmed"] == f"{amount}|2026-10-14"
                         and not after["recorded"]})
        report = {"passed": sum(r["passed"] for r in rows), "total": len(rows), "rows": rows,
                  "source": str(source), "source_call": str(CALL),
                  "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                  "workflow_sha256": hashlib.sha256(
                      (args.out / "workflow.json").read_bytes()).hexdigest(),
                  "compiler_changes": changes, "native_simulation": False,
                  "fresh_generation": False, "promoted": False}
        write_json(args.out / "report.json", report)
        return report

    result = capture_sync(args.out, "numeric_confirmation_repair", action)
    print(json.dumps({"passed": result["passed"], "total": result["total"]}))
    if result["passed"] != result["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
