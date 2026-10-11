"""Reject exported behavior changes while allowing pinned Freya defaults."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item

    return json.dumps(
        normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


# Observed unchanged in a-transfer-002/A, reference-builder-001/A and B,
# and astra-a-015-repair exports. New platform defaults require explicit review.
PLATFORM_CONFIG_SHA256 = "a9a19f3b7356d2b1b8668c249f0e4cf2a81167ab6e36990e073ed752e7f47439"
OFFICIAL_LLM = {"provider": "experimental-5", "model": "experimental-5"}
NODE_DEFAULTS = {
    "tools": [],
    "on_enter": {"type": "llm"},
    "assertions": [],
    "passthrough": False,
    "pre_actions": [],
    "transitions": [],
    "role_message": "",
    "interruptible": True,
    "max_node_turns": None,
    "response_mapping": [],
    "max_turn_transition": None,
    "truncation_strategy": "keep_all",
    "suppress_tool_results": False,
    "truncation_window_size": 10,
    "on_enter_first_entry_only": False,
    "disable_inactivity_monitor": False,
    "passthrough_default_transition": None,
    "return_to_previous_on_interrupt": False,
    "task_message": "",
    "extraction_schema": [],
}


def allowed_default(location: tuple[str, ...], key: str, value: Any) -> bool:
    if location == ("workflow",):
        if key in {"test_variables", "global_nodes"}:
            return canonical_json(value) == "{}"
        if key == "agent_config":
            canonical = canonical_json(value)
            return canonical == canonical_json({"llmConfig": OFFICIAL_LLM}) or (
                hashlib.sha256(canonical.encode()).hexdigest() == PLATFORM_CONFIG_SHA256
            )
        return False
    if len(location) < 3 or location[:2] not in {
        ("workflow", "nodes"),
        ("workflow", "global_nodes"),
    }:
        return False
    tail = location[3:]
    if not tail:
        return key in NODE_DEFAULTS and canonical_json(value) == canonical_json(NODE_DEFAULTS[key])
    if key == "description" and value == "":
        return tail in {
            ("pre_actions", "[]", "function"),
            ("tools", "[]", "function"),
            ("terminal", "function"),
            ("response_mapping", "[]"),
        }
    if key == "mode" and value == "exact" and tail[:3] == ("transitions", "[]", "condition"):
        rest = tail[3:]
        return len(rest) % 2 == 0 and all(
            rest[index : index + 2] == ("conditions", "[]") for index in range(0, len(rest), 2)
        )
    return False


def require_preserved(
    expected: Any,
    actual: Any,
    path: str = "workflow",
    *,
    _location: tuple[str, ...] = ("workflow",),
) -> None:
    """Normalize observed defaults; reject changed values and added behavior."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise ValueError(f"Export changed generated structure at {path}")
        for key, value in expected.items():
            if key not in actual:
                raise ValueError(f"Export omitted generated field at {path}.{key}")
            require_preserved(value, actual[key], f"{path}.{key}", _location=(*_location, key))
        for key in actual.keys() - expected.keys():
            if not allowed_default(_location, key, actual[key]):
                raise ValueError(f"Export added an unapproved field at {path}.{key}")
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            raise ValueError(f"Export changed generated list at {path}")
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            require_preserved(left, right, f"{path}.{index}", _location=(*_location, "[]"))
    elif canonical_json(expected) != canonical_json(actual):
        raise ValueError(f"Export changed generated value at {path}")
