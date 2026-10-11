"""Replay immutable Autobench records and verify their evidence references offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from autobench import build_report, replay_experiment


def audit(record: Path) -> dict:
    experiment = replay_experiment(record)
    build_report(experiment)
    rows = []
    for run in experiment.runs:
        artifacts = {artifact.name: artifact for artifact in run.task_result.artifacts}
        for artifact in artifacts.values():
            if not (record / str(artifact.value)).is_file():
                raise ValueError(f"Missing artifact payload: {artifact.name}")
        entries = []
        if manifest := artifacts.get("evidence-manifest"):
            entries = yaml.safe_load((record / str(manifest.value)).read_text())["payload"]
            for entry in entries:
                if entry.get("artifact", entry["path"]) not in artifacts:
                    raise ValueError(f"Missing evidence target: {entry['path']}")
        operations = sorted({span.operation for span in run.trace.spans}) if run.trace else []
        rows.append(
            {
                "status": str(run.status),
                "artifacts": len(artifacts),
                "evidence_paths": len(entries),
                "deduplicated_paths": sum(
                    entry.get("artifact", entry["path"]) != entry["path"] for entry in entries
                ),
                "operations": operations,
            }
        )
    return {"record": str(record), "replay_and_report": "passed", "runs": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path, nargs="+")
    args = parser.parse_args()
    for record in args.records:
        print(json.dumps(audit(record)))


if __name__ == "__main__":
    main()
