"""Repair B parsing defects observed in public/train native conversations."""

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


def repair_code(code: str) -> str:
    old = "for piece in re.split('[.;!?]', raw):"
    new = "for piece in re.split(r'[.;!?]|\\b(?:and|but|however)\\b', raw):"
    tree = ast.parse(code)
    aggregate = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "phase == 'all_today'"
        and old in (ast.get_source_segment(code, node) or "")
    ]
    assert len(aggregate) == 1
    section = ast.get_source_segment(code, aggregate[0])
    assert section and section.count(old) == 1
    replacement = section.replace(old, new, 1).replace(
        "piece = piece.strip()", "piece = piece.strip(' ,')", 1
    )
    code = code.replace(section, replacement, 1)

    anchor = "piece_scopes = [literal_scope(piece) for piece in pieces]"
    nodes = [
        node
        for node in ast.walk(ast.parse(code))
        if isinstance(node, ast.Assign) and ast.get_source_segment(code, node) == anchor
    ]
    assert len(nodes) == 1 and code.count(anchor) == 1
    indent = " " * nodes[0].col_offset
    lines = [
        "anchored_pieces = []",
        "for piece in pieces:",
        " previous = anchored_pieces[-1] if anchored_pieces else ''",
        " previous_ids = scoped(literal_scope(previous), False)",
        " same_dispute = bool(previous and len(previous_ids) == 1 "
        "and disputed(previous) and disputed(piece))",
        " refers_back = bool(re.match(r'^(?:that|this) "
        "(?:debt|loan|account|product)\\b', norm(piece)))",
        " if same_dispute and refers_back and not literal_scope(piece) "
        "and not any(date_candidates(piece)):",
        "  anchored_pieces[-1] = previous + '; ' + piece",
        " else:",
        "  anchored_pieces.append(piece)",
        "pieces = anchored_pieces",
        anchor,
    ]
    code = code.replace(anchor, ("\n" + indent).join(lines), 1)

    tree = ast.parse(code)
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "amount_shaped_day"
    ]
    assert len(functions) == 1
    function = functions[0]
    section = ast.get_source_segment(code, function)
    old = "return bare_day and (money_suffix or payment_amount)"
    assert section and section.count(old) == 1
    indent = " " * function.body[-1].col_offset
    pattern = (
        r"\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|[1-9]"
        r"|products?|loans?|cards?|accounts?|items?|ones?|payments?)\b"
    )
    replacement = section.replace(
        old,
        f"product_reference = bool(re.match({pattern!r}, text[match.end():]))\n"
        + indent
        + "return bare_day and (money_suffix or payment_amount or product_reference)",
        1,
    )
    code = code.replace(section, replacement, 1)
    ast.parse(code)
    return code


def repair_interrupted_corrections(code: str) -> str:
    """Require current speech evidence before changing terms or transferring."""
    old = "return bare_day and (money_suffix or payment_amount or product_reference)"
    assert code.count(old) == 1
    code = code.replace(
        old,
        "ordinal_reference = bool(re.search(r'\\b(?:keep|retain|leave)\\s+$', "
        "text[:match.start()]))\n"
        "   return bare_day and (money_suffix or payment_amount or "
        "product_reference or ordinal_reference)",
        1,
    )
    anchor = "for request_piece in re.split('[.;!?]|\\\\bbut\\\\b', raw, flags=re.I):"
    assert code.count(anchor) == 1
    # Extracted tags may be inferred from the prior negotiation. A transfer needs
    # evidence from this utterance; the following existing loop recognizes it.
    code = code.replace(
        anchor,
        "tags.discard('restructuring')\n   " + anchor,
        1,
    )
    old = "if request_intent and restructuring_terms and (not indirect_or_negative):"
    assert code.count(old) == 1
    code = code.replace(
        old,
        "if restructuring_terms and (request_intent or "
        "re.fullmatch(r'(?:restructuring|restructure)(?: please)?', request_words)) "
        "and (not indirect_or_negative):",
        1,
    )
    anchor = "elif s.get('verified') and (not raw) and (phase in "
    assert code.count(anchor) == 1
    # An unfinished correction is neither a refusal nor a new financial term.
    # Retain the current offer so the customer can finish it on the next turn.
    pattern = (
        r"\b(?:change|move|correct|amend)\s+(?:only(?:\s+|$))?(?:the\s+)?"
        r"(?:(?:credit\s+card|personal\s+loan|car\s+loan|card|loan|account|product)"
        r"(?:\s+ending\s+\d{4})?)?(?:\s+to)?\s*$"
    )
    replacement = (
        "elif s.get('verified') and phase in ['confirm', 'resolve_product'] "
        f"and (re.search({pattern!r}, raw_n) or "
        "re.search(r'\\b(?:change|move|correct|amend)\\b.*\\bto\\s+"
        "(?:[1-9]|[12][0-9]|3[01])$', raw_n)):\n"
        "    clarify_pending('Please finish the change you want to make. "
        "I have retained the existing amounts and dates, and have not recorded "
        "these promises.')\n   " + anchor
    )
    code = code.replace(anchor, replacement, 1)
    ast.parse(code)
    return code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def repair() -> dict:
        source = args.parent / "workflow.json"
        workflow = json.loads(source.read_text())
        field = workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"][
            "properties"
        ]["code"]
        field["default"] = repair_interrupted_corrections(repair_code(field["default"]))
        compiled, changes = compile_workflow(
            WorkflowBuild(
                workflow=workflow,
                rule_nodes=json.loads((args.parent / "rule-nodes.json").read_text()),
            )
        )
        write_json(args.out / "workflow.json", compiled.workflow)
        report = {
            "source": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "workflow_sha256": hashlib.sha256(
                (args.out / "workflow.json").read_bytes()
            ).hexdigest(),
            "changes": [
                "Recognize complete aggregate-today declines before conjunctions, "
                "including with existing extractor clauses.",
                "Distinguish product ordinal references from bare calendar dates "
                "without treating them as consent.",
                "Keep an adjacent 'that debt' restatement with the same explicitly "
                "scoped dispute instead of asking about a phantom extra product.",
                "Keep incomplete correction fragments from changing terms or "
                "excluding products; ground restructuring transfers in current speech.",
            ],
            "compiler_changes": changes,
            "fresh_generation": False,
            "promoted": False,
            "holdout_opened": False,
        }
        write_json(args.out / "provenance.json", report)
        return report

    capture_sync(args.out, "b_native_training_repairs", repair)
    print(args.out)


if __name__ == "__main__":
    main()
