"""Print a compact, read-only progress snapshot without opening any dataset."""

import argparse
import json
from pathlib import Path


def read(path: Path):
    return json.loads(path.read_text())


def status(root: Path) -> dict:
    candidates = sorted((root / "candidates").glob("*"))
    evaluations = sorted((root / "evaluations").glob("*"))
    result = {
        "builds_started": len(candidates),
        "builds_generated": sum((p / "workflow.json").exists() for p in candidates),
        "evaluations_started": len(evaluations),
        "reflections": len(list((root / "reflections").glob("[0-9][0-9][0-9].json"))),
    }
    if candidates:
        trace = candidates[-1] / "trace.jsonl"
        if trace.exists():
            # Ignore a partial final write rather than racing the active process.
            rows = trace.read_text().splitlines()
            for line in reversed(rows):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                result["latest_build"] = {
                    "name": candidates[-1].name,
                    **{
                        key: event[key]
                        for key in ("event", "time", "purpose", "status")
                        if key in event
                    },
                }
                break
    if evaluations:
        latest = evaluations[-1]
        repeats = sorted(latest.glob("repeat-*"))
        if repeats:
            report = repeats[-1] / "practice/results.json"
            state = repeats[-1] / "practice/simulation-state.json"
            result["latest_evaluation"] = {"name": latest.name, "repeat": repeats[-1].name}
            if report.exists():
                result["latest_evaluation"].update(
                    {key: read(report).get(key) for key in ("status", "passed", "total")}
                )
            elif state.exists():
                data = read(state)
                result["latest_evaluation"].update(
                    {"status": data.get("status"), "metrics": data.get("metrics")}
                )
    for name in ("aborted.json", "selection.json"):
        if (root / name).exists():
            data = read(root / name)
            result[name] = {
                key: data[key]
                for key in ("reason", "status", "validation_score", "builds")
                if key in data
            }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(status(args.directory)))
