"""Ground a generic today refusal in the immediately pending all-products offer."""

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
CALL = ROOT / ("runs/v13-b-financial-extraction-repeat-001/practice/"
               "call-413cb8c9-c684-441e-a340-b61308c9b1d3.json")


def code_field(workflow):
    return workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"][
        "properties"
    ]["code"]


def repair(code):
    tree = ast.parse(code)
    blocks = [n for n in ast.walk(tree) if isinstance(n, ast.If)
              and ast.unparse(n.test) == "phase == 'all_today'"
              and "for piece in re.split" in (ast.get_source_segment(code, n) or "")]
    assert len(blocks) == 1
    old = ast.get_source_segment(code, blocks[0])
    assert old is not None
    new = r'''def aggregate_today_refusal(piece, residual=False):
    piece = str(piece).strip(' ,')
    if not piece or piece[:1] in ('"', "'", '“', '‘') or '?' in piece:
     return False
    if literal_scope(piece) not in ('', 'all'):
     return False
    words = norm(piece)
    if residual:
     words = re.sub(r'\s+(?:and|but|however)$', '', words)
    return bool(re.fullmatch(r'(?:no )?(?:(?:i )?(?:cannot|can t|cant|will not|won t|wont) '
                             r'pay(?: all(?: of)?(?: that| those| them| it)?)? today|'
                             r'not today)', words))
   if phase == 'all_today':
    for piece in re.split(r'(?<=[.;!?])|\b(?:and|but|however)\b', raw, flags=re.I):
     piece = piece.strip(' ,')
     if aggregate_today_refusal(piece):
      if not any((norm(piece) == norm(c.get('evidence', '')) for c in original_clauses)):
       original_clauses.insert(0, dict(scope='all', decision='reject', evidence=piece))'''
    code = code.replace(old, new, 1)
    anchor = "retained = held['clauses']"
    assert code.count(anchor) == 1
    code = code.replace(anchor, anchor + r'''
    if phase == 'all_today':
     aggregate, specific = [], []
     for held_clause in retained:
      if not scoped(held_clause.get('scope', ''), False) and aggregate_today_refusal(
          held_clause.get('evidence', ''), residual=True):
       evidence = re.sub(r'\b(?:and|but|however)\b[\s.,;!?]*$', '',
                         str(held_clause.get('evidence', ''))).strip(' ,.;')
       held_clause.update(scope='all', decision='reject', date='', date_words='',
                          date_kind='', date_conflict=False, incomplete=False,
                          consent_evidence=False, evidence=evidence)
       aggregate.append(held_clause)
      else:
       specific.append(held_clause)
     retained = aggregate + specific''', 1)
    ast.parse(code)
    return code


def native_inputs():
    found = []
    for message in json.loads(CALL.read_text())["call"]["messages"]:
        try:
            event = json.loads(message["message"])
        except (ValueError, TypeError):
            continue
        values = event.get("variable_table", {})
        if (event.get("event") == "route_evaluated"
                and message.get("nodeId") == "dialogue_financial"
                and "not pay" in values.get("turn_raw", "").lower()):
            found.append(values)
            if len(found) == 2:
                return found
    raise ValueError("Missing actual initial/clarification inputs")


def probe(code, values):
    result = execute_python(code, {"vars": copy.deepcopy(values)})["vars"]
    state = json.loads(result["state"])
    return {"route": result["route"], "message": result["message"], "state": state}


def progressed(result):
    state = result["state"]
    card, personal, car = state["products"]
    return (state["phase"] == "negotiate" and not state.get("unprocessed_proposal")
            and card["status"] == "agreed" and card["amount"] == 510
            and card["date"] == "2026-10-15" and card["attempts"] == 0
            and personal["attempts"] == 1 and personal["date"] == "2026-10-16"
            and car["attempts"] == 0 and not any(p.get("confirmed") for p in state["products"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def action():
        workflow = json.loads(args.workflow.read_text())
        baseline = code_field(workflow)["default"]
        code_field(workflow)["default"] = repair(baseline)
        compiled, changes = compile_workflow(WorkflowBuild(
            workflow=workflow, rule_nodes=json.loads(args.rules.read_text())))
        write_json(args.out / "workflow.json", compiled.workflow)
        code = code_field(compiled.workflow)["default"]
        initial, clarification = native_inputs()
        rows = []
        for name, values in (("native_initial", initial), ("native_recovery", clarification)):
            old, new = probe(baseline, values), probe(code, values)
            rows.append({"id": name, "baseline_phase": old["state"]["phase"],
                         "candidate_phase": new["state"]["phase"],
                         "message": new["message"], "passed": progressed(new)})
        prefix = "I can't pay today"
        for replacement in ("I cannot pay today", "No, I can't pay today",
                            "I will not pay today", "I cannot pay all of them today",
                            "Can't pay today", "Not today", "No, not today"):
            values = copy.deepcopy(initial)
            values["turn_raw"] = values["turn_raw"].replace(prefix, replacement, 1)
            rows.append({"id": replacement, "passed": progressed(probe(code, values))})
        for replacement in ('"I can\'t pay today"', "I can't pay today?",
                            "He says I can't pay today", "If I can't pay today",
                            "I can't pay the car loan today", "I can't pay tomorrow"):
            values = copy.deepcopy(initial)
            values["turn_raw"] = values["turn_raw"].replace(prefix, replacement, 1)
            rows.append({"id": replacement,
                         "passed": probe(code, values) == probe(baseline, values)})
        for phase in ("negotiate", "confirm", "resolve_product"):
            values = copy.deepcopy(initial)
            state = json.loads(values["state"])
            state["phase"] = phase
            values["state"] = json.dumps(state)
            rows.append({"id": "other_phase_" + phase,
                         "passed": probe(code, values) == probe(baseline, values)})
        values = copy.deepcopy(clarification)
        state = json.loads(values["state"])
        state["unprocessed_proposal"]["phase"] = "negotiate"
        values["state"] = json.dumps(state)
        rows.append({"id": "recovery_does_not_invent_all_scope_for_single_offer",
                     "passed": probe(code, values) == probe(baseline, values)})
        report = {"passed": sum(r["passed"] is True for r in rows), "total": len(rows),
            "rows": rows, "source": str(args.workflow), "source_call": str(CALL),
            "source_sha256": hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            "call_sha256": hashlib.sha256(CALL.read_bytes()).hexdigest(),
            "workflow_sha256": hashlib.sha256(
                (args.out / "workflow.json").read_bytes()).hexdigest(),
            "compiler_changes": changes, "native_simulation": False,
            "fresh_generation": False, "promoted": False}
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "b_aggregate_scope_repair", action)
    print(json.dumps({"passed": report["passed"], "total": report["total"]}))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
