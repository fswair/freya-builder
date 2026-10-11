"""Check whether B shares A's explicit amount/date proposal-grounding defect."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    workflow = json.loads(args.workflow.read_text())
    fixture_path = ROOT / "tests/fixtures/b-partial-proposal-state.json"
    fixture = json.loads(fixture_path.read_text())

    def probe() -> dict:
        code = workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"][
            "properties"
        ]["code"]["default"]
        rows = []
        for raw in [
            "60 on Friday.",
            "Sixty on Friday.",
            "Sixty on Friday is what I had in mind; I cannot manage the whole amount.",
            "Sixty on Friday would work for me.",
        ]:
            values = copy.deepcopy(fixture)
            state = json.loads(values["state"])
            product = state["products"][0]
            product.update(
                status="open",
                amount=product["past_due"],
                date="2026-10-14",
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
            values.update({key: "" for key in values if key.startswith("turn_")})
            extractor = workflow["nodes"].get(
                "dialogue_financial", workflow["nodes"]["dialogue"]
            )
            for field in extractor["extraction_schema"]:
                options = field.get("enum_values", [])
                values[field["name"]] = next(
                    (item for item in ("none", "no", "unknown", "other") if item in options), ""
                )
            terms = [
                {
                    "scope": product["label"],
                    "amount": "60",
                    "date_words": "Friday",
                    "date_kind": "exact",
                    "decision": "none",
                    "evidence": raw,
                }
            ]
            values.update(
                state=json.dumps(state),
                turn_raw=raw,
                turn_scope=product["label"],
                turn_amount="60",
                turn_date_words="Friday",
                turn_date_kind="exact",
                turn_terms=json.dumps(terms),
            )
            after = execute_python(code, {"vars": values})["vars"]
            actual = json.loads(after["state"])["products"][0]
            no_write = after.get("route") != "create_promise" and actual.get("status") != "recorded"
            rows.append(
                {
                    "raw": raw,
                    "amount": actual["amount"],
                    "date": actual["date"],
                    "route": after.get("route"),
                    "status": actual.get("status"),
                    "no_write": no_write,
                    "passed": no_write
                    and actual["amount"] == 66.0
                    and actual["date"] == "2026-10-16",
                }
            )
        report = {
            "scope": "Four synthetic local B proposal probes; no native score or external request.",
            "workflow_sha256": hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
            "passed": sum(row["passed"] is True for row in rows),
            "total": len(rows),
            "rows": rows,
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "offline_b_proposal_phrasing", probe)
    print(json.dumps({k: report[k] for k in ("passed", "total")}))


if __name__ == "__main__":
    main()
