"""Stage a provenance-pinned development reference pair for source packaging."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import utc_now, write_json


def stage(single: Path, multi: Path, output: Path) -> dict:
    sources = {"single-product.json": single, "multi-product.json": multi}
    data = {}
    for name, source in sources.items():
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Reference must be a regular file: {name}")
        raw = source.read_bytes()
        workflow = json.loads(raw)
        if not isinstance(workflow.get("nodes"), dict) or not workflow.get("initial_node"):
            raise ValueError(f"Reference is not a workflow: {name}")
        data[name] = raw
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "created_at": utc_now(),
        "scope": "experimental public/train development references; not promoted",
        "official_submission": False,
        "holdout_opened": False,
        "sources": {},
    }
    for name, raw in data.items():
        destination = output / name
        destination.write_bytes(raw)
        expected = hashlib.sha256(raw).hexdigest()
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == expected
        manifest["sources"][name] = {
            "path": str(sources[name]),
            "sha256": expected,
            "bytes": len(raw),
        }
    write_json(output / "provenance.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("single", type=Path)
    parser.add_argument("multi", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = stage(args.single, args.multi, args.out)
    print(json.dumps(result))
