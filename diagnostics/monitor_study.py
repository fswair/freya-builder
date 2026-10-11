"""Print compact read-only study progress without opening held-out data."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def read(path: Path):
    return json.loads(path.read_text())


def monitor(root: Path) -> dict:
    builds, completed, active = [], [], []
    for trace in sorted(root.glob("candidates/*/trace.jsonl")):
        events = [
            json.loads(x) for x in trace.read_text().splitlines(keepends=True) if x.endswith("\n")
        ]
        last = events[-1] if events else {}
        end = next((x for x in events if x["event"] == "builder_end"), {})
        builds.append(
            {
                "name": trace.parent.name,
                "status": end.get("status") or last.get("purpose") or last.get("event"),
                "seconds": end.get("elapsed_seconds"),
            }
        )
    for directory in sorted(root.glob("evaluations/*")):
        result = directory / "evaluation.json"
        if result.exists():
            value = read(result)
            completed.append({"name": directory.name, "score": value["score"]})
            continue
        for state in sorted(directory.glob("repeat-*/practice/simulation-state.json")):
            value = read(state)
            active.append(
                {
                    "name": f"{directory.name}/{state.parent.parent.name}",
                    "status": value["status"],
                    "metrics": value.get("metrics"),
                }
            )
    return {
        "time": datetime.now(UTC).isoformat(),
        "builds": builds,
        "completed": completed,
        "active": active[-3:],
        "reflections": sum(p.stem.isdecimal() for p in root.glob("reflections/*.json")),
        "aborted": read(root / "aborted.json") if (root / "aborted.json").exists() else None,
        "selection": (
            read(root / "selection.json")["validation_score"]
            if (root / "selection.json").exists()
            else None
        ),
    }


if __name__ == "__main__":
    print(json.dumps(monitor(Path(sys.argv[1]))))
