"""Check published promise-date bounds on synthetic calendar boundary states."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
from datetime import date, timedelta
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.recording import capture_sync

ROOT = Path(__file__).resolve().parents[1]
HOLIDAYS = {"2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"}


def business(day: date) -> bool:
    return day.weekday() < 5 and day.isoformat() not in HOLIDAYS


def deadline(today: date, days_late: int) -> date:
    remaining = 12 if days_late <= 7 else 7
    result = today
    while remaining:
        result += timedelta(days=1)
        remaining -= business(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location(
        "a_fixture", ROOT / "tests/test_reference_a_v2.py"
    )
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    dates = [date(2026, 10, 12) + timedelta(days=i) for i in range(7)]
    dates += [date(2026, 11, 26), date(2026, 12, 25), date(2027, 1, 1)]

    def probe() -> dict:
        summaries = []
        for path in args.workflow:
            workflow = json.loads(path.read_text())
            code = workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"][
                "properties"
            ]["code"]["default"]
            rows = []
            for today in dates:
                for days_late in (3, 8):
                    values = inspect.unwrap(fixture.variables)(code)
                    values["now"] = today.isoformat() + "T11:00"
                    state = json.loads(values["call_state"])
                    product = state["products"]["p1"]
                    limit = deadline(today, days_late)
                    product.update(
                        days_late=days_late,
                        budget=1 if days_late <= 7 else 2,
                        deadline=limit.isoformat(),
                        date=today.isoformat(),
                    )
                    values["call_state"] = json.dumps(state)
                    result, _, product = fixture.turn(
                        code,
                        values,
                        "No, I will not pay.",
                        turn_intent="other",
                        turn_decision="reject",
                    )
                    proposed = date.fromisoformat(product["date"])
                    rows.append(
                        {
                            "today": today.isoformat(),
                            "days_late": days_late,
                            "deadline": limit.isoformat(),
                            "proposed": proposed.isoformat(),
                            "attempts": product["attempts"],
                            "pass": product["attempts"] == 1
                            and today <= proposed <= limit
                            and business(proposed),
                            "message": result.get("message", result.get("core_message", "")),
                        }
                    )
            summaries.append(
                {
                    "workflow": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "passed": sum(row["pass"] is True for row in rows),
                    "total": len(rows),
                    "rows": rows,
                }
            )
        report = {
            "scope": (
                "Synthetic advisory checks of Brief A rules 18 and 19; "
                "not native scoring or hidden data."
            ),
            "limits": (
                "Checks date eligibility, not an official interpretation "
                "of ambiguous weekend persuasion scheduling."
            ),
            "network_calls": False,
            "results": summaries,
        }
        write_json(args.out / "report.json", report)
        return report

    result = capture_sync(args.out, "offline_calendar_bounds", probe)
    print(
        json.dumps(
            [{k: row[k] for k in ("workflow", "passed", "total")} for row in result["results"]]
        )
    )


if __name__ == "__main__":
    main()
