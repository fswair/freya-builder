"""Replay spoken dates against the existing A holiday-calendar fixture."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from freya_hackathon.offline import run_suite
from freya_hackathon.practice_regressions import suite


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    fixtures = suite()
    seed = next(c for c in fixtures["cases"] if c["id"] == "holiday_moves_to_next_business_day")
    for index, words in enumerate(
        ["Monday the 26th", "twenty six October", "October twenty sixth", "26 October"], 1
    ):
        case = deepcopy(seed)
        case["id"] = f"spoken_holiday_date_{index}"
        for turn in case["turns"]:
            if turn["extracted"].get("turn_date") == "2026-10-26":
                turn["extracted"]["turn_date"] = words
        fixtures["cases"].append(case)
    (args.out / "suite.json").write_text(json.dumps(fixtures, indent=2))
    result = run_suite(json.loads(args.workflow.read_text()), fixtures)
    (args.out / "results.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "total": result["total"],
                "failures": [row for row in result["rows"] if not row["passed"]],
            }
        )
    )


if __name__ == "__main__":
    main()
