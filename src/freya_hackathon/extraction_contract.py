"""Align explicit empty resets with string extraction enums accepted by Freya."""

from __future__ import annotations

import ast
import re


def blank_reset_names(tree: ast.AST) -> set[str]:
    """Find literal resets and resets in loops over an unambiguous static key list.

    This deliberately does not guess dynamic keys or arbitrary Python data flow.
    The names describe resets present in the generated code, not policy outcomes.
    """
    assignments: dict[str, list[ast.AST | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assignments.setdefault(node.target.id, []).append(node.value)
        elif isinstance(node, (ast.AugAssign, ast.NamedExpr)) and isinstance(node.target, ast.Name):
            assignments.setdefault(node.target.id, []).append(None)
        elif isinstance(node, ast.arg):
            assignments.setdefault(node.arg, []).append(None)
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.For, ast.AsyncFor))
            and isinstance(node.target, ast.Name)
            and node.target.id in assignments
        ):
            assignments[node.target.id].append(None)

    def static_names(value: ast.AST, seen: frozenset[str] = frozenset()) -> set[str] | None:
        if isinstance(value, ast.Name):
            definitions = assignments.get(value.id, [])
            if value.id in seen or len(definitions) != 1 or definitions[0] is None:
                return None
            return static_names(definitions[0], seen | {value.id})
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)) and all(
            isinstance(item, ast.Constant) and isinstance(item.value, str) for item in value.elts
        ):
            return {
                item.value
                for item in value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
        return None

    class Resets(ast.NodeVisitor):
        def __init__(self) -> None:
            self.fields: set[str] = set()
            self.bindings: dict[str, set[str] | None] = {}

        def visit_For(self, node: ast.For) -> None:
            previous = self.bindings.copy()
            if isinstance(node.target, ast.Name):
                # A reassigned loop variable cannot be resolved to its iterator safely.
                self.bindings[node.target.id] = (
                    None if node.target.id in assignments else static_names(node.iter)
                )
            for statement in [*node.body, *node.orelse]:
                self.visit(statement)
            self.bindings = previous

        def visit_Assign(self, node: ast.Assign) -> None:
            if isinstance(node.value, ast.Constant) and node.value.value == "":
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "vars"
                    ):
                        key = target.slice
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            self.fields.add(key.value)
                        elif isinstance(key, ast.Name):
                            self.fields.update(self.bindings.get(key.id) or ())
            self.generic_visit(node)

    visitor = Resets()
    visitor.visit(tree)
    return visitor.fields


def permit_blank_enum_resets(workflow: dict, reset_fields: set[str]) -> list[dict]:
    """Mutate only matching string extraction enums in an already-copied workflow."""
    changes = []
    for group in ("nodes", "global_nodes"):
        for node_id, node in workflow.get(group, {}).items():
            for index, field in enumerate(node.get("extraction_schema", [])):
                name = field.get("name")
                choices = field.get("enum_values")
                if (
                    name not in reset_fields
                    or field.get("type") != "string"
                    or not isinstance(choices, list)
                ):
                    continue
                pattern = field.get("validation_pattern")
                if pattern is not None:
                    try:
                        accepts_empty = re.search(pattern, "") is not None
                    except re.error as exc:
                        raise ValueError(f"Invalid extraction pattern for {name}") from exc
                    if not accepts_empty:
                        raise ValueError(
                            f"Blank reset for {name} conflicts with its extraction pattern; "
                            "use a compatible neutral reset and schema"
                        )
                if "" in choices:
                    continue
                field["enum_values"] = [*choices, ""]
                changes.append(
                    {
                        "path": [group, node_id, "extraction_schema", index, "enum_values"],
                        "fix": "permit_explicit_blank_reset",
                        "variable": name,
                    }
                )
    return changes
