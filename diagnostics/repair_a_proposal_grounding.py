"""Preserve grounded amounts in current first-person proposals with date/politeness wording."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.recording import capture_sync


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def repair() -> dict:
        workflow = json.loads(args.workflow.read_text())
        field = workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"][
            "properties"
        ]["code"]
        code = field["default"]
        old = "amount_tokens = normalized(cleaned).split()"
        nodes = [
            node
            for node in ast.walk(ast.parse(code))
            if isinstance(node, ast.Assign) and ast.get_source_segment(code, node) == old
        ]
        assert len(nodes) == 1 and code.count(old) == 1
        indentation = " " * nodes[0].col_offset
        tail = (
            r"\s+(?:(?:is|was)\s+what\s+(?:I|we)\s+(?:had|have)\s+in\s+mind"
            r"|(?:would|will)\s+work\s+for\s+(?:me|us)|works?\s+for\s+(?:me|us)"
            r"|is\s+(?:my|our)\s+(?:proposal|offer))\s*[.!]?\s*$"
        )
        date_glue = r"\b(?:on|by|for)\s*[.,;!]?\s*$"
        lines = [
            "proposal_surface = cleaned.strip()",
            "if day and normalized(day) in normalized(clause):",
            " proposal_surface = re.sub(" + repr(date_glue) + ", '', proposal_surface, flags=re.I)",
            "proposal_surface = re.sub(" + repr(tail) + ", '', proposal_surface, flags=re.I)",
            "if day and normalized(day) in normalized(clause):",
            " proposal_surface = re.sub(" + repr(date_glue) + ", '', proposal_surface, flags=re.I)",
            "amount_tokens = normalized(proposal_surface).split()",
        ]
        code = code.replace(old, ("\n" + indentation).join(lines), 1)
        constants = [
            node
            for node in ast.walk(ast.parse(code))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith(r"\b(?:too much|")
            and "unable to) pay" in node.value
        ]
        assert len(constants) == 1
        node = constants[0]
        assert isinstance(node.value, str)
        old_pattern = ast.get_source_segment(code, node)
        assert old_pattern and code.count(old_pattern) == 1
        new_pattern = node.value.replace("unable to) pay", "unable to) (?:pay|manage|cover)")
        code = code.replace(old_pattern, repr(new_pattern), 1)
        ast.parse(code)
        field["default"] = code
        write_json(args.out / "workflow.json", workflow)
        report = {
            "scope": "Experimental A grounding repair from training evidence; no native promotion.",
            "source": str(args.workflow),
            "source_sha256": hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            "workflow_sha256": hashlib.sha256(
                (args.out / "workflow.json").read_bytes()
            ).hexdigest(),
            "changes": [
                "Remove neutral first-person tails only for the existing bare-amount guard.",
                "Remove trailing on/by/for only when the explicit date was present in that clause.",
                "Recognize cannot manage/cover the full/whole amount as full-amount inability.",
            ],
            "preserved": [
                "Positive-clause filtering, exact numeric grounding and explicit date exclusion.",
                "Product/card exclusion, confirmation and bank acknowledgement requirements.",
                "All bank actions, schemas and workflow transitions.",
            ],
            "fresh_generation": False,
            "promoted": False,
        }
        write_json(args.out / "provenance.json", report)
        return report

    capture_sync(args.out, "a_proposal_grounding_repair", repair)
    print(args.out)


if __name__ == "__main__":
    main()
