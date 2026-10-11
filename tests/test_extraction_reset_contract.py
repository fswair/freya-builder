"""Regression coverage for observed rejection of per-turn enum resets."""

import ast
from copy import deepcopy

import pytest

from freya_hackathon import extraction_contract as CONTRACT


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("vars['decision'] = ''", {"decision"}),
        (
            "keys = ['decision', 'date_kind']\nfor k in keys:\n vars[k] = ''",
            {"decision", "date_kind"},
        ),
        ("for k in ('decision',):\n vars[k] = ''", {"decision"}),
        ("keys: list[str] = ['decision']\nfor k in keys:\n vars[k] = ''", {"decision"}),
        ("keys = ['decision']\nkeys = load_keys()\nfor k in keys:\n vars[k] = ''", set()),
        ("for k in load_keys():\n vars[k] = ''", set()),
        ("for k in ['decision']:\n k = unknown()\n vars[k] = ''", set()),
        ("keys = ['decision']\ndef helper(keys):\n for k in keys:\n  vars[k] = ''", set()),
        ("keys = ['decision']\nfor keys in unknown():\n for k in keys:\n  vars[k] = ''", set()),
        ("other['decision'] = ''\nvars['date_kind'] = 'none'", set()),
    ],
)
def test_explicit_reset_detection(code, expected):
    assert CONTRACT.blank_reset_names(ast.parse(code)) == expected


def test_schema_alignment_is_scoped_and_idempotent():
    workflow = {
        "nodes": {
            "dialogue": {
                "extraction_schema": [
                    {"name": "decision", "type": "string", "enum_values": ["accept", "none"]},
                    {"name": "unreset", "type": "string", "enum_values": ["a", "b"]},
                    {"name": "number", "type": "integer", "enum_values": [1, 2]},
                ],
                "tools": [{"parameters": {"reason": {"enum": ["paid", "refused"]}}}],
            }
        }
    }
    original = deepcopy(workflow)
    result = deepcopy(workflow)
    changes = CONTRACT.permit_blank_enum_resets(result, {"decision", "number"})
    assert [change["variable"] for change in changes] == ["decision"]
    schema = result["nodes"]["dialogue"]["extraction_schema"]
    assert schema[0]["enum_values"] == ["accept", "none", ""]
    assert schema[1:] == original["nodes"]["dialogue"]["extraction_schema"][1:]
    assert result["nodes"]["dialogue"]["tools"] == original["nodes"]["dialogue"]["tools"]
    assert CONTRACT.permit_blank_enum_resets(result, {"decision"}) == []
    assert workflow == original


@pytest.mark.parametrize("pattern", ["[", "accept|none"])
def test_incompatible_extraction_pattern_is_not_silently_relaxed(pattern):
    workflow = {
        "global_nodes": {
            "dialogue": {
                "extraction_schema": [
                    {
                        "name": "decision",
                        "type": "string",
                        "enum_values": ["accept", "none"],
                        "validation_pattern": pattern,
                    }
                ]
            }
        }
    }
    with pytest.raises(ValueError):
        CONTRACT.permit_blank_enum_resets(workflow, {"decision"})


def test_accepted_reset_prevents_stale_consent_on_next_turn():
    code = (
        "vars['action'] = 'write' if vars.get('decision') == 'accept' else 'clarify'\n"
        "keys = ['decision']\n"
        "for key in keys:\n"
        " vars[key] = ''\n"
    )
    workflow = {
        "nodes": {
            "dialogue": {
                "extraction_schema": [
                    {"name": "decision", "type": "string", "enum_values": ["accept", "none"]}
                ]
            }
        }
    }

    def turn(table: dict, choices: list) -> dict:
        updated = deepcopy(table)
        exec(code, {"vars": updated})
        # Reproduce the platform's recorded merge: reject values outside the enum.
        return {
            **table,
            **{
                key: value
                for key, value in updated.items()
                if key != "decision" or value in choices
            },
        }

    old_choices = workflow["nodes"]["dialogue"]["extraction_schema"][0]["enum_values"]
    old = turn({"decision": "accept"}, old_choices)
    assert turn(old, old_choices)["action"] == "write"
    CONTRACT.permit_blank_enum_resets(workflow, CONTRACT.blank_reset_names(ast.parse(code)))
    new_choices = workflow["nodes"]["dialogue"]["extraction_schema"][0]["enum_values"]
    new = turn({"decision": "accept"}, new_choices)
    assert new["decision"] == ""
    assert turn(new, new_choices)["action"] == "clarify"
