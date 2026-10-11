"""Extend a direct amount correction's lexical guard to ordinary spoken numerals."""

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
        matches = [
            node
            for node in ast.walk(ast.parse(code))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("^(?:(?:no|sorry|actually)")
            and node.value.endswith(r"\d")
        ]
        assert len(matches) == 1
        node = matches[0]
        assert isinstance(node.value, str)
        before = ast.get_source_segment(code, node)
        assert before and code.count(before) == 1
        words = (
            "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
            "thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
            "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
        )
        pattern = node.value[:-2] + r"(?:\d|(?:" + words + r")\b)"
        field["default"] = code.replace(before, repr(pattern), 1)
        ast.parse(field["default"])
        write_json(args.out / "workflow.json", workflow)
        provenance = {
            "source": str(args.workflow),
            "source_sha256": hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            "workflow_sha256": hashlib.sha256(
                (args.out / "workflow.json").read_bytes()
            ).hexdigest(),
            "before_pattern": node.value,
            "after_pattern": pattern,
            "scope": "Manual training-derived lexical correction; no native score.",
            "limits": "Existing numeric grounding, date exclusion and consent gates are preserved.",
            "fresh_generation": False,
            "promoted": False,
        }
        write_json(args.out / "provenance.json", provenance)
        return provenance

    capture_sync(args.out, "spoken_restatement_repair", repair)
    print(args.out)


if __name__ == "__main__":
    main()
