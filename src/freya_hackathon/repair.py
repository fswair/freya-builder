"""Apply model-authored, exact-match text edits to a copied workflow build."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from freya_hackathon.policy import StrictModel
from freya_hackathon.workflow import WorkflowBuild


class TextEdit(StrictModel):
    path: list[str | int] = Field(
        min_length=2, description="Path starting with workflow or rule_nodes"
    )
    before: str = Field(
        min_length=1, description="Exact substring occurring once at this text path"
    )
    after: str = Field(description="Replacement text; preserve unrelated behavior")


class WorkflowTextRepair(StrictModel):
    edits: list[TextEdit] = Field(min_length=1, max_length=64)

    def apply(self, previous: WorkflowBuild, *, allow_noop: bool = False) -> WorkflowBuild:
        data = previous.model_dump()
        for index, edit in enumerate(self.edits):
            if edit.path[0] not in {"workflow", "rule_nodes"}:
                raise ValueError("Text edit must target workflow or rule_nodes")
            target: Any = data
            try:
                for part in edit.path:
                    if isinstance(target, list):
                        if type(part) is not int or not 0 <= part < len(target):
                            raise ValueError("Text edit has an invalid list index")
                    elif not isinstance(target, dict) or not isinstance(part, str):
                        raise ValueError("Text edit has an invalid object path")
                    parent, key = target, part
                    target = target[part]
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError("Text edit path does not exist") from exc
            if not isinstance(target, str) or target.count(edit.before) != 1:
                matches = target.count(edit.before) if isinstance(target, str) else "not a string"
                raise ValueError(
                    f"Text edit must match exactly once in a string field; "
                    f"edit {index}, path {edit.path!r}, matches {matches}"
                )
            if edit.before == edit.after:
                if not allow_noop:
                    raise ValueError("Text edit must change its target")
                continue
            parent[key] = target.replace(edit.before, edit.after, 1)
        return WorkflowBuild.model_validate(data)
