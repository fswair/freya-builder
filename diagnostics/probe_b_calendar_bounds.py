"""Check B's first persuasion offer against published date eligibility bounds."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from probe_calendar_bounds import business, deadline

from freya_hackathon.builder import write_json
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    fixture = (ROOT / "tests/fixtures/b-partial-proposal-state.json").read_text()
    dates = [date(2026, 10, 12) + timedelta(days=i) for i in range(7)]
    dates += [date(2026, 11, 26), date(2026, 12, 25), date(2027, 1, 1)]

    def probe() -> dict:
        summaries = []
        for path in args.workflow:
            workflow = json.loads(path.read_text())
            code = workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"][
                "properties"
            ]["code"]["default"]
            rows = []
            for today in dates:
                for days_late in (3, 8):
                    values = json.loads(fixture)
                    state = json.loads(values["state"])
                    product = state["products"][-1]
                    limit = deadline(today, days_late)
                    product.update(
                        days_late=days_late,
                        budget=1 if days_late <= 7 else 2,
                        deadline=limit.isoformat(),
                        status="open",
                        amount=product["past_due"],
                        date=today.isoformat(),
                        attempts=0,
                        refused=[],
                        unaffordable=False,
                        date_conflict=False,
                        offer_kind="full_today",
                        pending_kind="",
                        pending_text="",
                    )
                    state.update(
                        products=[product],
                        active=0,
                        phase="negotiate",
                        unprocessed_proposal={},
                        pending_clauses=[],
                        notices=[],
                    )
                    raw = "No, I will not pay today."
                    values.update(
                        now=today.isoformat() + "T11:00",
                        state=json.dumps(state),
                        turn_raw=raw,
                        turn_decision="reject",
                        turn_scope="car loan",
                        turn_terms=json.dumps(
                            [{"scope": "car loan", "decision": "reject", "evidence": raw}]
                        ),
                    )
                    result = execute_python(code, {"vars": values})["vars"]
                    product = json.loads(result["state"])["products"][0]
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
            "scope": "Synthetic advisory date-eligibility checks; no native score.",
            "limits": "Not an official interpretation of ambiguous weekend persuasion scheduling.",
            "results": summaries,
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "offline_b_calendar_bounds", probe)
    print(
        json.dumps([{k: r[k] for k in ("workflow", "passed", "total")} for r in report["results"]])
    )


if __name__ == "__main__":
    main()
