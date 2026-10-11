"""Clarify partial consent explicitly without widening its financial scope."""

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

CALL = Path("diagnostics/v17-b008-live-read-001/practice/"
            "call-846f5e20-9976-4d95-a1bf-86d41729e274.json")


def code_field(workflow):
    return workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"][
        "properties"]["code"]


def seed():
    for message in json.loads(CALL.read_text())["call"]["messages"]:
        try:
            event = json.loads(message.get("message", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        values = event.get("variable_table", {})
        if (event.get("event") == "route_evaluated" and event.get("target") == "decide"
                and values.get("turn_raw") == "I confirm the credit card for 220."):
            return values
    raise ValueError("Native partial confirmation input missing")


def repair(code):
    lines = code.splitlines()
    matches = [i for i, line in enumerate(lines)
               if "if phase=='confirm' and any_consent and batch_consent" in line]
    assert len(matches) == 1
    start = matches[0]
    assert lines[start + 1].strip() == "consent_batch()"
    assert lines[start + 2].strip() == "else:"
    indent = lines[start][:len(lines[start]) - len(lines[start].lstrip())]
    addition = [
        "elif (phase=='confirm' and any_consent and not replayed",
        "      and consent_ids and consent_ids < required_consent",
        "      and not negative and not changed and not unresolved",
        "      and vars.get('last_core_message','') == s.get('readback_text','')):",
        " accepted_labels=', '.join(ps[i]['label'] for i in sorted(consent_ids))",
        " remaining_labels=', '.join(ps[i]['label']",
        "                            for i in sorted(required_consent-consent_ids))",
        " clarify_pending('You have confirmed only '+accepted_labels+'. '",
        "                 'Do you also confirm the unchanged amounts and dates for '",
        "                 +remaining_labels+", 
        "                 ', so I can record all the promises just read back?')",
    ]
    lines[start + 2:start + 2] = [indent + line for line in addition]
    return "\n".join(lines) + ("\n" if code.endswith("\n") else "")


def run(code, values):
    return execute_python(code, {"vars": copy.deepcopy(values)})["vars"]


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
        for raw, scope, amount in ((template["turn_raw"], "credit card", 220),
                                   ("I confirm the personal loan for 213.", "personal loan", 213),
                                   ("I confirm the car loan for 75.", "car loan", 75)):
            values = copy.deepcopy(template)
            values.update(turn_raw=raw, turn_scope=scope, turn_topics="", turn_decision="accept",
                          turn_terms=json.dumps([dict(scope=scope, decision="accept",
                                                     amount=amount, evidence=raw)]))
            old, new = run(baseline, values), run(code, values)
            state = json.loads(new["state"])
            before = json.loads(values["state"])
            passed = (new["route"] == "dialogue" and state["phase"] == "confirm"
                      and "You have confirmed only " in new["message"]
                      and "so I can record all the promises just read back?" in new["message"]
                      and state["products"] == before["products"]
                      and state["confirmation_batch"] == before["confirmation_batch"]
                      and new["last_core_message"] == values["last_core_message"]
                      and old["message"] != new["message"]
                      and not any(p["confirmed"] for p in state["products"]))
            rows.append({"id": scope, "passed": passed, "candidate_message": new["message"]})
            # A later complete yes must still confirm exactly the read-back batch.
            following = copy.deepcopy(new)
            for name in list(following):
                if name.startswith("turn_"):
                    following[name] = ""
            following.update(turn_raw="Yes, I confirm all those promises.",
                             turn_decision="accept", turn_scope="all",
                             turn_terms=json.dumps([dict(scope="all", decision="accept",
                                  evidence="Yes, I confirm all those promises.")]))
            result = run(code, following)
            after = json.loads(result["state"])
            rows.append({"id": scope + "_then_complete_confirmation",
                         "passed": result["route"] == "create_promise"
                         and all(p["confirmed"] for p in after["products"])
                         and [(p["amount"], p["date"]) for p in after["products"]]
                         == [(p["amount"], p["date"]) for p in before["products"]],
                         "route": result["route"]})
        for name in ("complete_yes", "changed_amount", "changed_date", "rejection",
                     "quoted", "question", "unknown_product", "stale_readback",
                     "not_confirmation", "unverified"):
            values = copy.deepcopy(template)
            state = json.loads(values["state"])
            raw, scope, dec = values["turn_raw"], "credit card", "accept"
            extra = {"amount": 220}
            if name == "complete_yes":
                raw, scope, extra = "Yes, I confirm all those promises.", "all", {}
            elif name == "changed_amount":
                raw, extra = "I confirm the credit card for 221.", {"amount": 221}
            elif name == "changed_date":
                raw = "I confirm the credit card on 15 October."
                extra = {"date": "2026-10-15", "date_words": "15 October"}
            elif name == "rejection":
                raw, dec, extra = "I cannot pay the credit card.", "reject", {}
            elif name == "quoted":
                raw = 'He said "I confirm the credit card for 220."'
            elif name == "question":
                raw = "Should I confirm the credit card for 220?"
            elif name == "unknown_product":
                raw, scope, extra = "I confirm that one.", "that one", {}
            elif name == "stale_readback":
                values["last_core_message"] = "A different amount was discussed."
            elif name == "not_confirmation":
                state["phase"] = "negotiate"
            elif name == "unverified":
                state.update(verified=False, phase="opening")
            values.update(state=json.dumps(state), turn_raw=raw, turn_scope=scope,
                          turn_topics="", turn_decision=dec,
                          turn_terms=json.dumps([dict(scope=scope, decision=dec,
                                                     evidence=raw, **extra)]))
            old, new = run(baseline, values), run(code, values)
            rows.append({"id": name, "passed": old == new,
                         "candidate_route": new["route"]})
        report = {"passed": sum(r["passed"] for r in rows), "total": len(rows), "rows": rows,
                  "source": str(source), "source_call": str(CALL),
                  "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                  "workflow_sha256": hashlib.sha256(
                      (args.out / "workflow.json").read_bytes()).hexdigest(),
                  "compiler_changes": changes, "native_simulation": False,
                  "fresh_generation": False, "promoted": False}
        write_json(args.out / "report.json", report)
        return report

    result = capture_sync(args.out, "partial_confirmation_clarification", action)
    print(json.dumps({"passed": result["passed"], "total": result["total"]}))
    if result["passed"] != result["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
