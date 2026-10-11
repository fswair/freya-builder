"""Copy predeclared priority training cases and unchanged development validation."""

import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import ROOT, write_json


def prepare(source: Path, output: Path) -> None:
    manifest = json.loads((source / "manifest.json").read_text())
    priority = manifest["priority_training_ids"]
    rows = json.loads((source / "train.json").read_text())
    by_id = {row["id"]: row for row in rows}
    if len(set(priority)) != 16 or len(priority) != 16 or set(priority) - by_id.keys():
        raise ValueError("Expected sixteen unique predeclared priority training IDs")
    selected = [by_id[case_id] for case_id in priority]
    if any(sum(row["brief"] == brief for row in selected) != 8 for brief in ("A", "B")):
        raise ValueError("Expected eight priority cases per brief")
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "train.json", selected)
    validation = (source / "validation.json").read_bytes()
    (output / "validation.json").write_bytes(validation)
    write_json(
        output / "subset-provenance.json",
        {
            "source": str(source),
            "selection": "Original dataset manifest priority_training_ids; not chosen by scores",
            "training_ids": priority,
            "source_train_sha256": hashlib.sha256((source / "train.json").read_bytes()).hexdigest(),
            "source_manifest_sha256": hashlib.sha256(
                (source / "manifest.json").read_bytes()
            ).hexdigest(),
            "validation_sha256": hashlib.sha256(validation).hexdigest(),
            "holdout_opened": False,
        },
    )


if __name__ == "__main__":
    prepare(ROOT / "datasets/freya-v2/live", ROOT / "datasets/freya-v2/live-priority")
