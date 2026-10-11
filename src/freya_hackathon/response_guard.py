"""Reject retained mapped API data after a failed Freya request.

Freya can update ``<node>_response.status`` to error while retaining the prior
response's data keys. A whole-data mapping then overwrites the pre-call reset
with that old success. Track the latest mapped request and check its current
transport status before any Python controller consumes the mapped payload.
"""

from __future__ import annotations

import ast
import json

from freya_hackathon.code_scope import STEP
from freya_hackathon.workflow import walk

MARKER = "_builder_response_guard_v1"
GUARD = """_builder_response_guard_v1 = True
_builder_status_key = vars.get('_builder_api_mapping_status_key', '')
if _builder_status_key and vars.get(_builder_status_key) != 'success':
    import json as _builder_response_json
    _builder_targets = vars.get('_builder_api_mapping_targets', '[]')
    for _builder_target in _builder_response_json.loads(_builder_targets):
        vars[_builder_target] = '{}'
"""


def _prepend_guard(code: str) -> str:
    tree = ast.parse(code)
    if any(isinstance(node, ast.Name) and node.id == MARKER for node in ast.walk(tree)):
        return code
    if tree.body and isinstance(tree.body[0], ast.FunctionDef) and tree.body[0].name == STEP:
        # Preserve compact reference indentation and the sandbox's input-size budget.
        first = tree.body[0].body[0]
        lines = code.splitlines(keepends=True)
        guard = "".join(" " * first.col_offset + line + "\n" for line in GUARD.splitlines())
        lines.insert(first.lineno - 1, guard)
        return "".join(lines)
    return GUARD + "\n" + code


def guard_api_mapping_errors(workflow: dict) -> list[dict]:
    code_fields = []
    for _, value in walk(workflow):
        if not isinstance(value, dict):
            continue
        function = value.get("function", {})
        if function.get("handler") == "__function__:code_exec":
            field = function.get("parameters", {}).get("properties", {}).get("code", {})
            if isinstance(field.get("default"), str):
                code_fields.append(field)
    if not code_fields:
        return []
    changes = []
    mapped = False
    nodes = {**workflow.get("nodes", {}), **workflow.get("global_nodes", {})}
    for node_id, node in nodes.items():
        targets = [
            item["variable"]
            for item in node.get("response_mapping", [])
            if item.get("path") in ("data", "data._builder_payload_json")
            and isinstance(item.get("variable"), str)
        ]
        actions = [
            action
            for action in node.get("pre_actions", [])
            if action.get("function", {}).get("handler") == "__function__:api_call"
        ]
        if not targets or len(actions) != 1:
            continue
        mapped = True
        action = actions[0]
        code = action.get("pre_process_code", "")
        if "_builder_api_mapping_status_key" in code:
            continue
        status_key = f"{node_id}_response.status"
        action["pre_process_code"] = (
            code.rstrip()
            + "\n"
            + f"vars['_builder_api_mapping_status_key'] = {status_key!r}\n"
            + f"vars['_builder_api_mapping_targets'] = {json.dumps(targets)!r}\n"
            + f"vars[{status_key!r}] = 'pending'\n"
        )
        changes.append({"node": node_id, "fix": "track_current_api_mapping_status"})
    if mapped:
        for field in code_fields:
            guarded = _prepend_guard(field["default"])
            if guarded != field["default"]:
                field["default"] = guarded
                changes.append({"fix": "reject_stale_mapped_api_payload"})
    return changes
