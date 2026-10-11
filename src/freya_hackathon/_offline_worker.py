"""Isolated, bounded worker for reviewed workflow snippets; not a security sandbox."""

from __future__ import annotations

import ast
import builtins
import json
import resource
import sys

MODULES = {"json", "datetime", "re", "math", "decimal", "calendar", "collections", "copy"}
BUILTINS = (
    "abs all any bool chr dict divmod enumerate filter float int isinstance issubclass iter len "
    "list map max min next range reversed round set sorted str sum tuple type zip "
    "Exception ValueError TypeError KeyError IndexError ZeroDivisionError"
).split()


def run(payload: dict) -> dict:
    tree = ast.parse(payload["code"])
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
            if any(name not in MODULES for name in names) or getattr(node, "level", 0):
                raise ValueError("Unsupported import in offline snippet")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("Private attribute access is unsupported")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError("Private names are unsupported")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and hasattr(builtins, node.func.id)
            and node.func.id not in BUILTINS
        ):
            raise ValueError(f"Unsupported offline builtin: {node.func.id}")

    def import_module(name, globals=None, locals=None, fromlist=(), level=0):
        if name not in MODULES or level:
            raise ValueError("Unsupported import")
        return builtins.__import__(name, globals, locals, fromlist, level)

    safe = {name: getattr(builtins, name) for name in BUILTINS}
    safe["__import__"] = import_module
    namespace = {**payload["values"], "__builtins__": safe}
    exec(compile(tree, "<reviewed-workflow>", "exec"), namespace)
    return {name: namespace[name] for name in payload["values"]}


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    # Darwin does not support lowering RLIMIT_AS for this Python distribution.
    # CPU and parent wall-clock limits still apply; this is not a security sandbox.
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    try:
        result = {"values": run(json.load(sys.stdin))}
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, allow_nan=False))
