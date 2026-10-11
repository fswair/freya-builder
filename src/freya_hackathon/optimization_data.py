"""Strict live-case ingestion; training never opens the held-out case file."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from freya_hackathon.builder import KIT, Inputs, load_inputs


class BriefSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: list[Path] = Field(min_length=1)
    tool_api: Path


class StudyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: Path
    public_cases: Path
    briefs: dict[str, BriefSpec]
    guidance: Path
    workflow_references: list[Path] = Field(default_factory=list, max_length=8)
    model: str = "gpt-6-astra"
    reasoning: Literal["low", "medium", "high", "xhigh"] = "medium"
    metric_calls: int = Field(default=8, ge=4, le=100)
    max_builds: int = Field(default=6, ge=1, le=50)
    max_reflections: int = Field(default=2, ge=1, le=20)
    study_seconds: float = Field(default=7200, gt=0, le=86400)
    generation_seconds: float = Field(default=1200, gt=0, le=1800)
    practice_seconds: float = Field(default=900, gt=0, le=1800)
    train_repetitions: int = Field(default=1, ge=1, le=3)
    validation_repetitions: int = Field(default=3, ge=1, le=3)

    @classmethod
    def read(cls, path: Path) -> StudyConfig:
        config = cls.model_validate_json(path.read_text())
        base = path.resolve().parent
        config.dataset = (base / config.dataset).resolve()
        config.public_cases = (base / config.public_cases).resolve()
        config.guidance = (base / config.guidance).resolve()
        config.workflow_references = [(base / p).resolve() for p in config.workflow_references]
        for brief in config.briefs.values():
            brief.documents = [(base / p).resolve() for p in brief.documents]
            brief.tool_api = (base / brief.tool_api).resolve()
        if not config.briefs or set(config.briefs) - {"A", "B"}:
            raise ValueError("Live optimization supports registered A/B bank fixtures only")
        return config

    def inputs(self) -> dict[str, Inputs]:
        return {
            key: load_inputs(
                spec.documents, spec.tool_api, KIT, workflow_references=self.workflow_references
            )
            for key, spec in self.briefs.items()
        }


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def load_split(config: StudyConfig, split: str) -> list[dict[str, Any]]:
    if split not in {"train", "validation", "holdout"}:
        raise ValueError("Unknown dataset split")
    path = config.dataset / f"{split}.json"
    if path.stat().st_size > 5_000_000:
        raise ValueError("Case file exceeds 5 MB")
    rows = json.loads(path.read_text())
    seeds = {c["id"]: c for c in json.loads(config.public_cases.read_text())}
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{split} must be a nonempty case array")
    required = {"id", "brief", "variables", "identity", "products", "script", "checks", "expected"}
    required.update({"family", "level", "title"})
    for row in rows:
        if not isinstance(row, dict) or required - row.keys():
            raise ValueError(f"Incomplete case in {split}")
        if not isinstance(row["id"], str) or not row["id"].strip():
            raise ValueError("Case ID must be a nonempty string")
        if not isinstance(row.get("practice_session_case"), str):
            raise ValueError("Practice session alias must be a registered case ID")
        seed = seeds.get(row.get("practice_session_case"))
        if seed is None or row["id"] in seeds:
            raise ValueError("Each DEV case needs a registered practice_session_case")
        if set(row) - set(seed) - {"practice_session_case", "metadata"}:
            raise ValueError("Unrecognized live case fields; use metadata for annotations")
        if not isinstance(row["brief"], str) or row["brief"] not in config.briefs:
            raise ValueError("No source documents configured for this brief")
        for field in ("brief", "variables", "identity", "products"):
            if row[field] != seed[field]:
                raise ValueError(f"{row['id']} changed bank fixture field {field}")
        metadata = row.get("metadata", {})
        if (
            not isinstance(metadata, dict)
            or metadata.get("split") != split
            or not isinstance(metadata.get("group_id"), str)
            or not metadata["group_id"].strip()
        ):
            raise ValueError(f"{row['id']} needs matching split and a scenario group_id")
        if not isinstance(row["script"], str) or not row["script"].strip():
            raise ValueError("Customer script is empty")
        if not isinstance(row["checks"], list) or not all(
            isinstance(item, str) and item.strip() for item in row["checks"]
        ):
            raise ValueError("Conversation checks must be strings")
        if (
            not isinstance(row["expected"], dict)
            or {"outcome", "reason", "promises", "notes", "callbacks", "transfers"}
            - row["expected"].keys()
        ):
            raise ValueError("Expected ledger outcome is incomplete")
    return rows


def registry(rows: list[dict]) -> dict[str, list[str]]:
    return {
        "ids": [str(row["id"]) for row in rows],
        "groups": sorted({row["metadata"]["group_id"] for row in rows}),
        "scripts": [fingerprint(" ".join(row["script"].lower().split())) for row in rows],
    }


def assert_disjoint(registries: dict[str, dict[str, list[str]]]) -> None:
    seen: dict[str, set[str]] = {"ids": set(), "groups": set(), "scripts": set()}
    for split, values in registries.items():
        for key in seen:
            if key != "groups" and len(values[key]) != len(set(values[key])):
                raise ValueError(f"Duplicate {key} within {split}")
            if seen[key].intersection(values[key]):
                raise ValueError(f"Cross-split leakage: {key} in {split}")
            seen[key].update(values[key])


def load_development(config: StudyConfig) -> dict[str, list[dict]]:
    # Do not stat, parse, hash or index holdout.json here.
    splits = {split: load_split(config, split) for split in ("train", "validation")}
    assert_disjoint({split: registry(rows) for split, rows in splits.items()})
    for split, rows in splits.items():
        if {row["brief"] for row in rows} != set(config.briefs):
            raise ValueError(f"{split} must cover every configured brief")
    return splits
