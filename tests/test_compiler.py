import json

import pytest

from freya_hackathon.compiler import compile_workflow
from freya_hackathon.workflow import WorkflowBuild


def test_object_mapping_becomes_lossless_scalar_and_is_idempotent() -> None:
    draft = WorkflowBuild(
        workflow={
            "nodes": {
                "lookup": {
                    "node_type": "api_request",
                    "pre_actions": [
                        {
                            "function": {
                                "handler": "__function__:api_call",
                                "parameters": {
                                    "properties": {"method": {"type": "string", "const": "POST"}}
                                },
                            }
                        }
                    ],
                    "response_mapping": [{"path": "data", "variable": "payload"}],
                }
            }
        },
        rule_nodes={},
    )
    compiled, changes = compile_workflow(draft)
    node = compiled.workflow["nodes"]["lookup"]
    result = {"data": {"ok": True, "products": [{"amount": 120.5}]}}
    expected = json.dumps(result["data"])
    exec(node["pre_actions"][0]["post_process_code"], {"result": result})
    assert result["data"]["_builder_payload_json"] == expected
    assert node["response_mapping"][0]["path"] == "data._builder_payload_json"
    assert draft.workflow["nodes"]["lookup"]["response_mapping"][0]["path"] == "data"
    assert node["pre_actions"][0]["function"]["parameters"]["properties"]["method"] == {
        "type": "string",
        "default": "POST",
    }
    assert len(changes) == 2
    again, changes = compile_workflow(compiled)
    assert again == compiled and not changes


def retired_node() -> dict:
    return {
        "node_type": "code",
        "on_enter": {"type": "silent"},
        "passthrough": True,
        "passthrough_default_transition": "end",
        "pre_actions": [],
    }


def retired_draft() -> WorkflowBuild:
    return WorkflowBuild(
        workflow={
            "initial_node": "start",
            "nodes": {
                "start": {"node_type": "conversation", "transitions": [{"target_node": "end"}]},
                "end": {"node_type": "end_call"},
                "retired": retired_node(),
            },
            "global_nodes": {},
        },
        rule_nodes={"prohibition": ["start", "retired"], "closing": ["end"]},
    )


def test_retired_placeholder_is_removed_without_changing_reachable_behavior() -> None:
    draft = retired_draft()
    compiled, changes = compile_workflow(draft)
    assert compiled.workflow["nodes"] == {
        key: node for key, node in draft.workflow["nodes"].items() if key != "retired"
    }
    assert compiled.rule_nodes == {"prohibition": ["start"], "closing": ["end"]}
    assert "retired" in draft.workflow["nodes"]
    assert changes == [
        {"node": "retired", "fix": "remove_unreachable_silent_stub", "rules": ["prohibition"]}
    ]
    assert compile_workflow(compiled) == (compiled, [])


def test_global_entry_points_and_their_normal_descendants_are_preserved() -> None:
    draft = retired_draft()
    draft.workflow["global_nodes"] = {
        "interrupt": {"node_type": "conversation", "transitions": [{"target_node": "retired"}]}
    }
    compiled, changes = compile_workflow(draft)
    assert compiled == draft and changes == []


def test_retired_placeholder_cannot_erase_a_rules_only_implementation() -> None:
    draft = retired_draft()
    draft.rule_nodes["prohibition"] = ["retired"]
    with pytest.raises(ValueError, match="no reachable policy implementation"):
        compile_workflow(draft)


def test_unreachable_executable_nodes_are_not_silently_removed() -> None:
    draft = retired_draft()
    draft.workflow["nodes"]["retired"]["post_process_code"] = "vars['meaningful'] = True"
    compiled, changes = compile_workflow(draft)
    assert compiled == draft and changes == []


def test_pruning_does_not_hide_an_invalid_transition() -> None:
    draft = retired_draft()
    draft.workflow["nodes"]["retired"]["passthrough_default_transition"] = "unknown"
    compiled, changes = compile_workflow(draft)
    assert compiled == draft and changes == []


def test_consumed_extractions_clear_even_when_runtime_merges_vars() -> None:
    code = "for key in ('intent', 'amount'):\n    vars.pop(key, None)\n"
    draft = WorkflowBuild(
        workflow={
            "nodes": {
                "step": {
                    "extraction_schema": [
                        {"name": "intent", "type": "string", "enum_values": ["proposal", "none"]}
                    ],
                    "pre_actions": [
                        {
                            "function": {
                                "handler": "__function__:code_exec",
                                "parameters": {
                                    "properties": {
                                        "code": {"default": code},
                                    }
                                },
                            }
                        }
                    ],
                }
            }
        },
        rule_nodes={},
    )
    compiled, changes = compile_workflow(draft)
    code = compiled.workflow["nodes"]["step"]["pre_actions"][0]["function"]["parameters"][
        "properties"
    ]["code"]["default"]
    persistent = {"intent": "proposal", "amount": "100", "verified": True}
    local = persistent.copy()
    exec(code, {"vars": local})
    persistent.update(local)
    assert persistent == {"intent": "", "amount": "", "verified": True}
    assert {change["fix"] for change in changes} == {
        "assign_empty_to_reset_variables",
        "isolate_code_exec_scope",
        "permit_explicit_blank_reset",
    }
    schema = compiled.workflow["nodes"]["step"]["extraction_schema"][0]
    assert schema["enum_values"] == ["proposal", "none", ""]
    assert draft.workflow["nodes"]["step"]["extraction_schema"][0]["enum_values"] == [
        "proposal",
        "none",
    ]
    again, changes = compile_workflow(compiled)
    assert again == compiled and not changes
