"""Create an experimental smaller financial extractor; preserve other phases."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.compiler import compile_workflow
from freya_hackathon.recording import capture_sync
from freya_hackathon.workflow import WorkflowBuild

PHASES = ("all_today", "negotiate", "confirm", "resolve_product", "clarify_financial")
FIELDS = (
    "turn_amount", "turn_date", "turn_date_kind", "turn_date_words",
    "turn_change_evidence", "turn_paid_evidence",
)


def specialize(workflow, removed):
    nodes = workflow["nodes"]
    original = nodes["dialogue"]
    assert original["node_type"] == "conversation"
    assert not {"dialogue_full", "dialogue_financial"}.intersection(nodes)
    original_fields = {field["name"] for field in original["extraction_schema"]}
    assert set(removed).issubset(original_fields)
    full = copy.deepcopy(original)
    full["name"] = "dialogue_full"
    financial = copy.deepcopy(original)
    financial["name"] = "dialogue_financial"
    financial["extraction_schema"] = [
        field for field in financial["extraction_schema"] if field["name"] not in removed
    ]
    # Explicitly clear omitted inputs inside the consumer too. Local replay gates
    # supply all old fields; this makes those gates exercise the reduced contract.
    field = nodes["decide"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]
    anchor = "s = obj(vars.get('state', '{}'), {})"
    assert field["default"].count(anchor) == 1
    field["default"] = field["default"].replace(
        anchor,
        anchor + f"\n  if s.get('phase') in {PHASES!r}:\n"
        + f"   for _pruned_field in {tuple(removed)!r}:\n"
        + "    t[_pruned_field] = ''",
        1,
    )
    ast.parse(field["default"])
    nodes["dialogue_full"] = full
    nodes["dialogue_financial"] = financial
    nodes["dialogue"] = {
        "name": "dialogue",
        "node_type": "code",
        "on_enter": {"type": "silent"},
        "passthrough": True,
        "passthrough_default_transition": "dialogue_full",
        "pre_actions": [],
        "transitions": [
            {
                "target_node": "dialogue_financial",
                "description": "Use the reduced extractor only for financial negotiation.",
                "condition": {
                    "logic": "or",
                    "conditions": [
                        {"variable": "phase", "operator": "equals", "operand": phase}
                        for phase in PHASES
                    ],
                },
            },
            {
                "target_node": "dialogue_full",
                "description": "Preserve extraction for identity, callbacks and other stages.",
                "condition": {"operator": "always"},
            },
        ],
    }
    # All customer-facing actions and outgoing routes remain byte-for-byte copies.
    for key in ("on_enter", "task_message", "transitions"):
        assert original.get(key) == full.get(key) == financial.get(key)
    assert [field["name"] for field in full["extraction_schema"]] == [
        field["name"] for field in original["extraction_schema"]
    ]
    return workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--remove", nargs="+", choices=FIELDS, default=list(FIELDS))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def action():
        workflow = specialize(json.loads(args.workflow.read_text()), args.remove)
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
            "removed_in_financial_phases": args.remove,
            "financial_phases": PHASES,
            "other_phases_changed": False,
            "compiler_changes": changes,
            "new_llm_calls_in_router": 0,
            "native_latency_test": "not run",
            "local_behavior_gates": "pending",
            "fresh_generation": False,
            "promoted": False,
        }
        write_json(args.out / "provenance.json", report)
        return report

    capture_sync(args.out, "financial_extractor_specialization", action)
    print(args.out)


if __name__ == "__main__":
    main()
