"""Local structural checks. These do not prove platform or policy correctness."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from typing import Any

from pydantic import Field

from freya_hackathon.policy import Policy, StrictModel


class WorkflowBuild(StrictModel):
    workflow: dict[str, Any]
    rule_nodes: dict[str, list[str]] = Field(
        description="Every policy rule ID mapped to implementation nodes, plus unresolved-1, "
        "unresolved-2, etc. mapped to safe dialogue/end boundaries for each policy limitation"
    )


def walk(value: Any, path: str = "$") -> Iterator[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")


def transition_targets(node: dict[str, Any]) -> set[str]:
    targets = {
        item["target_node"]
        for _, item in walk(node)
        if isinstance(item, dict) and "target_node" in item
    }
    for key in ("max_turn_transition", "passthrough_default_transition"):
        if isinstance(node.get(key), str) and node[key]:
            targets.add(node[key])
    return targets


def resolve(value: dict[str, Any], api: dict[str, Any]) -> dict[str, Any]:
    seen: set[str] = set()
    while "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            raise ValueError("Only acyclic local OpenAPI references are supported")
        seen.add(ref)
        target: Any = api
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        if not isinstance(target, dict):
            raise ValueError("OpenAPI reference must resolve to an object")
        value = {**target, **{k: v for k, v in value.items() if k != "$ref"}}
    return value


def api_operations(api: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    operations = {}
    for path, raw_item in api["paths"].items():
        item = resolve(raw_item, api)
        for method, operation in item.items():
            if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            servers = operation.get("servers", item.get("servers", api.get("servers", [])))
            for server in servers:
                base = server["url"]
                for name, variable in server.get("variables", {}).items():
                    base = base.replace("{" + name + "}", str(variable["default"]))
                if not base.startswith("https://") or "{" in base:
                    raise ValueError("Bank API requires an explicit HTTPS server URL")
                operations[(method.upper(), base.rstrip("/") + "/" + path.lstrip("/"))] = operation
    if not operations:
        raise ValueError("Tool API has no supported operations")
    return operations


def constant(schema: dict[str, Any]) -> Any:
    if "const" in schema:
        return schema["const"]
    return schema.get("default")


def check_python(code: str, path: str) -> None:
    if "{{" in code or "}}" in code:
        raise ValueError(f"{path}: Python must use vars, not template interpolation")
    try:
        ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"{path}: invalid Python syntax at line {exc.lineno}") from None


def validate_workflow(draft: WorkflowBuild, policy: Policy, api: dict[str, Any]) -> dict[str, Any]:
    workflow = draft.workflow
    if "agent_config" in workflow:
        raise ValueError("Omit agent_config; the kit sets the fixed Freya model")
    nodes = workflow.get("nodes")
    globals_ = workflow.get("global_nodes", {})
    if not isinstance(nodes, dict) or not nodes or not isinstance(globals_, dict):
        raise ValueError("nodes and global_nodes must be objects; nodes cannot be empty")
    if set(nodes) & set(globals_):
        raise ValueError("Node IDs must be unique across normal and global nodes")
    all_nodes = {**nodes, **globals_}
    if workflow.get("initial_node") not in nodes:
        raise ValueError("initial_node must identify a normal node")
    if not isinstance(workflow.get("system_prompt", ""), str):
        raise ValueError("system_prompt must be a string when present")
    boundary_ids = policy.boundary_ids()
    if boundary_ids & {rule.id for rule in policy.rules}:
        raise ValueError("Policy rule IDs collide with reserved unresolved boundary IDs")
    if set(draft.rule_nodes) != {rule.id for rule in policy.rules} | boundary_ids:
        raise ValueError(
            "rule_nodes must cover every policy rule and unresolved boundary, without extra IDs"
        )
    for rule, targets in draft.rule_nodes.items():
        if not targets or set(targets) - set(all_nodes):
            raise ValueError(f"{rule}: missing or unknown implementation nodes")
        if rule in boundary_ids:
            boundary_nodes = [all_nodes[target] for target in targets]
            if any(not isinstance(node, dict) for node in boundary_nodes):
                raise ValueError(f"{rule}: boundary nodes must be objects")
            if not any(
                node.get("node_type") in {"conversation", "end_call"} for node in boundary_nodes
            ):
                raise ValueError(f"{rule}: limitation needs an explicit dialogue or end boundary")
            if any(
                isinstance(item, dict) and item.get("handler") == "__function__:api_call"
                for node in boundary_nodes
                for _, item in walk(node)
            ) or any(node.get("node_type") == "api_request" for node in boundary_nodes):
                raise ValueError(f"{rule}: unresolved boundary nodes cannot call a business API")

    edges: dict[str, set[str]] = {}
    for node_id, node in all_nodes.items():
        if not isinstance(node, dict):
            raise ValueError(f"{node_id}: node must be an object")
        kind = node.get("node_type")
        if kind not in {"conversation", "code", "api_request", "end_call"}:
            raise ValueError(f"{node_id}: unsupported node_type {kind}")
        if "loop_prompt" in node:
            raise ValueError(
                f"{node_id}: current Freya API rejects loop_prompt; preserve repeat-turn "
                "guidance in task_message using explicit dialogue state"
            )
        if kind == "conversation" and node.get("pre_actions"):
            raise ValueError(f"{node_id}: conversation pre_actions would be silently dropped")
        if kind != "conversation" and node.get("tools"):
            raise ValueError(f"{node_id}: tools would be silently dropped from action node")
        targets = transition_targets(node)
        allowed = set(all_nodes) | ({"__previous__"} if node_id in globals_ else set())
        if targets - allowed:
            raise ValueError(f"{node_id}: unknown transition targets {sorted(targets - allowed)}")
        edges[node_id] = targets - {"__previous__"}
        if node.get("passthrough") and kind != "end_call":
            transitions = node.get("transitions", [])
            has_default = bool(node.get("passthrough_default_transition")) or any(
                t.get("condition", {}).get("operator") == "always" for t in transitions
            )
            if not has_default:
                raise ValueError(f"{node_id}: passthrough node needs a fallback transition")
    reached: set[str] = set()
    queue = [workflow["initial_node"], *globals_]
    while queue:
        node_id = queue.pop()
        if node_id not in reached:
            reached.add(node_id)
            queue.extend(edges[node_id] - reached)
    if set(nodes) - reached:
        raise ValueError(f"Unreachable normal nodes: {sorted(set(nodes) - reached)}")

    operations = api_operations(api)
    tool_count = 0
    used_operations: set[tuple[str, str]] = set()
    for path, item in walk(workflow):
        if item is None:
            raise ValueError(f"{path}: omit null fields")
        if not isinstance(item, dict):
            continue
        for key in ("pre_process_code", "post_process_code"):
            if isinstance(item.get(key), str) and item[key]:
                check_python(item[key], f"{path}.{key}")
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        tool_count += 1
        handler = function.get("handler")
        if handler not in {
            "__function__:api_call",
            "__function__:code_exec",
            "__function__:end_call",
        }:
            raise ValueError(f"{path}: unsupported handler {handler}")
        if "each_turn" in item.get("config", {}).get("alwaysRunsAt", []):
            raise ValueError(f"{path}: each_turn lifecycle is not implemented by Freya")
        properties = function.get("parameters", {}).get("properties", {})
        if handler == "__function__:code_exec":
            code = constant(properties.get("code", {}))
            if not isinstance(code, str) or not code:
                raise ValueError(f"{path}: code_exec requires fixed Python code")
            check_python(code, path)
        if handler != "__function__:api_call":
            continue
        url = constant(properties.get("url", {}))
        method = constant(properties.get("method", {}))
        if not isinstance(url, str) or not isinstance(method, str):
            raise ValueError(f"{path}: API URL and method must be fixed in the schema")
        operation = operations.get((method.upper(), url))
        if operation is None:
            raise ValueError(f"{path}: API destination/method is outside supplied OpenAPI")
        used_operations.add((method.upper(), url))
        body = properties.get("body", {}).get("properties", {})
        request = resolve(operation.get("requestBody", {}), api)
        expected = resolve(
            request.get("content", {}).get("application/json", {}).get("schema", {}), api
        )
        missing = set(expected.get("required", [])) - set(body)
        if missing:
            raise ValueError(f"{path}: missing required bank tool fields {sorted(missing)}")
        for name, parameter in body.items():
            expected_parameter = resolve(expected.get("properties", {}).get(name, {}), api)
            allowed_values = expected_parameter.get("enum")
            if allowed_values is not None:
                actual_values = parameter.get("enum", [])
                fixed = constant(parameter)
                if set(actual_values) - set(allowed_values):
                    raise ValueError(f"{path}: unsupported enum value in {name}")
                if fixed is not None and "{{" not in str(fixed) and fixed not in allowed_values:
                    raise ValueError(f"{path}: unsupported fixed value in {name}")
    # Recording call outcomes is part of the hackathon's bank-tool contract. The
    # allowed fields/values still come from each brief's supplied API, never this code.
    close_operations = {
        key
        for key, operation in operations.items()
        if operation.get("operationId") == "close_call" or key[1].endswith("/close_call")
    }
    if close_operations and not close_operations & used_operations:
        raise ValueError(
            "Missing bank close_call API tool; Freya end_call does not record an outcome"
        )
    return {
        "status": "locally_validated",
        "scope": "structure, source references and declared API destinations",
        "node_count": len(all_nodes),
        "tool_count": tool_count,
        "rule_count": len(policy.rules),
        "unresolved_policy_count": len(policy.unresolved),
        "freya_validation": "not_run",
        "behavioral_evaluation": "not_run",
        "limitations": [
            "Rule coverage is declared by the model; semantic completeness is not proven.",
            "Python snippets are syntax-checked, not executed or security-sandboxed here.",
            "Official platform validation and customer simulations are still required.",
            "Unresolved policy boundaries are declared, not proof of complete business support.",
        ],
    }
