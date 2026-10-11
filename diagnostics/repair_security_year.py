"""Reject an incomplete or contradictory literal birth year before verification."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.compiler import compile_workflow
from freya_hackathon.recording import capture_sync
from freya_hackathon.workflow import WorkflowBuild

HELPER = r'''def _builder_unfinished_birth_year(speech, answer):
 import re as _birth_re
 speech, answer = str(speech or ''), str(answer or '')
 spoken_years = _birth_re.findall(r'\b(?:18|19|20)\d{2}\b', speech)
 if not spoken_years and _birth_re.search(r'\b\d{3}\b', speech):
  return True
 extracted_years = _birth_re.findall(r'\b(?:18|19|20)\d{2}\b', answer)
 return bool(spoken_years and extracted_years and set(spoken_years) != set(extracted_years))
'''


def repair(code: str, brief: str) -> str:
    tree = ast.parse(code)
    function = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_builder_code_step"
    )
    indent = " " * function.body[0].col_offset
    lines = code.splitlines()
    lines[function.body[0].lineno - 1: function.body[0].lineno - 1] = [
        indent + line for line in HELPER.splitlines()
    ]
    code = "\n".join(lines) + "\n"
    if brief == "A":
        anchor = "elif answer or numeric_dob:"
        check = "_builder_unfinished_birth_year(raw_turn, answer or numeric_dob)"
        reply = (
            "tell('security', 'Ask the customer to finish or clarify the birth year "
            "and repeat the complete birth date with the month as a word. Do not "
            "guess a missing digit or reuse a year from an earlier answer. "
            "This incomplete answer is not a wrong-answer attempt.')"
        )
    else:
        anchor = "elif t.get('turn_answer'):"
        check = "_builder_unfinished_birth_year(raw, t.get('turn_answer', ''))"
        reply = (
            "speak('Please finish the birth year and repeat your complete date of birth "
            "with the month as a word.', 'security', 'Complete birth date; no inferred year.')"
        )
    assert code.count(anchor) == 1
    code = code.replace(
        anchor,
        "elif s.get('security_field') == 'date_of_birth' and "
        + check
        + ":\n     "
        + reply
        + "\n    "
        + anchor,
        1,
    )
    ast.parse(code)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def action():
        workflow = json.loads(args.workflow.read_text())
        brief = "B" if "decide" in workflow["nodes"] else "A"
        controller = "decide" if brief == "B" else "advance"
        field = workflow["nodes"][controller]["pre_actions"][0]["function"]["parameters"][
            "properties"
        ]["code"]
        field["default"] = repair(field["default"], brief)
        compiled, changes = compile_workflow(
            WorkflowBuild(workflow=workflow, rule_nodes=json.loads(args.rules.read_text()))
        )
        write_json(args.out / "workflow.json", compiled.workflow)
        report = {
            "source": str(args.workflow),
            "source_sha256": hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            "workflow_sha256": hashlib.sha256(
                (args.out / "workflow.json").read_bytes()
            ).hexdigest(),
            "brief": brief,
            "compiler_changes": changes,
            "purpose": "Ask for a complete birth year rather than send an extractor guess",
            "limits": "Literal numeric-year grounding; not a complete speech/date verifier",
            "native_simulation": False,
            "fresh_generation": False,
            "promoted": False,
        }
        write_json(args.out / "provenance.json", report)
        return report

    capture_sync(args.out, "security_year_grounding_repair", action)
    print(args.out)


if __name__ == "__main__":
    main()
