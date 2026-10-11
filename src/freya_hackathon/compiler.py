"""Normalize generated workflows for observed Freya runtime serialization behavior."""

from __future__ import annotations

import ast
from copy import deepcopy

from freya_hackathon.code_scope import isolate_code
from freya_hackathon.code_size import compact_code
from freya_hackathon.extraction_contract import blank_reset_names, permit_blank_enum_resets
from freya_hackathon.response_guard import guard_api_mapping_errors
from freya_hackathon.workflow import WorkflowBuild, transition_targets, walk


class ResetVariables(ast.NodeTransformer):
    def visit_Expr(self, node: ast.Expr):
        call = node.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "vars"
            and call.func.attr == "pop"
            and 1 <= len(call.args) <= 2
            and not call.keywords
        ):
            # Freya merges returned keys into its variable table; deletion does not clear it.
            return ast.copy_location(
                ast.Assign(
                    targets=[
                        ast.Subscript(
                            value=ast.Name(id="vars", ctx=ast.Load()),
                            slice=call.args[0],
                            ctx=ast.Store(),
                        )
                    ],
                    value=ast.Constant(value=""),
                ),
                node,
            )
        return node


def prune_retired_stubs(draft: WorkflowBuild) -> list[dict]:
    """Remove unreachable silent placeholders, never executable policy mechanisms."""
    normal = draft.workflow.get("nodes", {})
    globals_ = draft.workflow.get("global_nodes", {})
    initial = draft.workflow.get("initial_node")
    if (
        not isinstance(normal, dict)
        or not isinstance(globals_, dict)
        or not isinstance(initial, str)
        or initial not in normal
    ):
        return []
    nodes = {**normal, **globals_}
    if set(normal) & set(globals_) or any(not isinstance(node, dict) for node in nodes.values()):
        return []  # Leave malformed graphs to the structural validator.
    edges = {key: transition_targets(node) for key, node in nodes.items()}
    for key, targets in edges.items():
        allowed = set(nodes) | ({"__previous__"} if key in globals_ else set())
        if targets - allowed:
            return []  # Pruning must not conceal an invalid destination.
    reached: set[str] = set()
    queue = [initial, *globals_]
    while queue:
        key = queue.pop()
        if key not in reached:
            reached.add(key)
            queue.extend(edges[key] - reached - {"__previous__"})
    retired = {
        key
        for key, node in normal.items()
        if key not in reached
        and node.get("node_type") == "code"
        and node.get("on_enter") == {"type": "silent"}
        and node.get("passthrough") is True
        and not any(
            isinstance(value, dict)
            and any(key in value for key in ("function", "pre_process_code", "post_process_code"))
            for _, value in walk(node)
        )
        and not any(
            node.get(field)
            for field in (
                "pre_actions",
                "post_actions",
                "tools",
                "extraction_schema",
                "response_mapping",
                "task_message",
            )
        )
    }
    for rule, targets in draft.rule_nodes.items():
        if set(targets) & retired and not set(targets) & reached:
            raise ValueError(f"{rule}: retired nodes have no reachable policy implementation")
    changes = []
    for key in sorted(retired):
        del normal[key]
        rules = [rule for rule, targets in draft.rule_nodes.items() if key in targets]
        for rule in rules:
            draft.rule_nodes[rule] = [target for target in draft.rule_nodes[rule] if target != key]
        changes.append({"node": key, "fix": "remove_unreachable_silent_stub", "rules": rules})
    return changes


def compile_workflow(draft: WorkflowBuild) -> tuple[WorkflowBuild, list[dict]]:
    result = draft.model_copy(deep=True)
    changes: list[dict] = prune_retired_stubs(result)
    reset_fields: set[str] = set()
    for path, value in walk(result.workflow):
        if not isinstance(value, dict):
            continue
        function = value.get("function", {})
        if function.get("handler"):
            for parameter_path, parameter in walk(function.get("parameters", {})):
                if isinstance(parameter, dict) and "const" in parameter:
                    parameter["default"] = parameter.pop("const")
                    changes.append(
                        {"path": path + parameter_path, "fix": "bake_constant_as_default"}
                    )
        if function.get("handler") == "__function__:code_exec":
            schema = function.get("parameters", {}).get("properties", {}).get("code", {})
            field = "const" if "const" in schema else "default"
            code = schema.get(field)
            if isinstance(code, str):
                try:
                    original = ast.parse(code)
                except SyntaxError as exc:
                    raise ValueError(
                        f"{path}: invalid Python syntax at line {exc.lineno}"
                    ) from None
                rewritten = ast.fix_missing_locations(ResetVariables().visit(deepcopy(original)))
                reset_fields.update(blank_reset_names(rewritten))
                if ast.dump(original) != ast.dump(rewritten):
                    schema[field] = ast.unparse(rewritten) + "\n"
                    changes.append({"path": path, "fix": "assign_empty_to_reset_variables"})
                isolated = isolate_code(schema[field])
                if isolated != schema[field]:
                    schema[field] = isolated
                    changes.append({"path": path, "fix": "isolate_code_exec_scope"})

    changes.extend(guard_api_mapping_errors(result.workflow))
    nodes = {**result.workflow.get("nodes", {}), **result.workflow.get("global_nodes", {})}
    for node_id, node in nodes.items():
        locations = [(node, node.get("pre_actions", []))]
        locations += [(tool, [tool]) for tool in node.get("tools", [])]
        for owner, actions in locations:
            mappings = [m for m in owner.get("response_mapping", []) if m.get("path") == "data"]
            if not mappings:
                continue
            api_actions = [
                a
                for a in actions
                if a.get("function", {}).get("handler") == "__function__:api_call"
            ]
            if len(api_actions) != 1:
                raise ValueError("Whole-data mapping requires exactly one API action")
            action = api_actions[0]
            # Whole objects are flattened by Freya. Map a scalar containing lossless JSON instead.
            hook = (
                "import json\n"
                "if isinstance(result, dict) and isinstance(result.get('data'), dict):\n"
                "    _payload = json.dumps(result['data'])\n"
                "    result['data']['_builder_payload_json'] = _payload\n"
            )
            action["post_process_code"] = action.get("post_process_code", "").rstrip() + "\n" + hook
            for mapping in mappings:
                mapping["path"] = "data._builder_payload_json"
            changes.append({"node": node_id, "fix": "serialize_whole_data_mapping"})
    changes.extend(permit_blank_enum_resets(result.workflow, reset_fields))
    # Large controllers share a 128KiB sandbox input limit with runtime data.
    # Compact only after all source rewrites; retain an identical AST.
    for path, value in walk(result.workflow):
        if not isinstance(value, dict):
            continue
        function = value.get("function", {})
        if function.get("handler") != "__function__:code_exec":
            continue
        field = function.get("parameters", {}).get("properties", {}).get("code", {})
        code = field.get("default")
        if not isinstance(code, str) or len(code.encode()) < 65536:
            continue
        compact = compact_code(code)
        if compact != code:
            field["default"] = compact
            changes.append({"path": path, "fix": "compact_code_exec_layout",
                            "saved_bytes": len(code.encode()) - len(compact.encode())})
    return result, changes
