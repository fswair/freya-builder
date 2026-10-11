"""Replay observed B extraction shapes; this does not test Freya's extractor."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from freya_hackathon.offline import Replay, check, run_suite
from freya_hackathon.practice_b_regressions import suite


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--stale-call", type=Path, help="Saved PUB-12 refusal-loop transcript")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    workflow = json.loads(args.workflow.read_text())
    fixtures = suite(workflow)
    # New extractors require verbatim evidence for already-paid claims. Preserve
    # each fixture's intended claim when adapting to that declared schema.
    fields = {field["name"] for field in workflow["nodes"]["dialogue"]["extraction_schema"]}
    if "turn_paid_evidence" in fields:
        for case in fixtures["cases"]:
            for step in case["turns"]:
                extracted = step["extracted"]
                if "paid" in extracted.get("turn_topics", "").split(","):
                    extracted.update(
                        turn_raw="I have already paid the overdue amount.",
                        turn_paid_evidence="I have already paid the overdue amount.",
                    )
    if "turn_raw" in fields:
        for case in fixtures["cases"]:
            for step in case["turns"]:
                extracted = step["extracted"]
                if extracted.get("turn_raw"):
                    continue
                topic = extracted.get("turn_topics")
                if extracted.get("turn_date"):
                    raw = f"I will pay all of them on {extracted['turn_date']}."
                    extracted["turn_change_evidence"] = raw
                elif topic == "dispute":
                    raw = "I dispute that debt. That is not my account."
                    extracted["turn_dispute_evidence"] = raw
                elif topic == "restructuring":
                    raw = "I need a new repayment plan."
                elif topic == "done":
                    raw = "No, thank you. That is all."
                elif extracted.get("turn_decision") == "accept":
                    raw = "Yes, I confirm."
                else:
                    raise ValueError(f"Missing spoken fixture for {case['id']}")
                extracted["turn_raw"] = raw
    seed = next(c for c in fixtures["cases"] if c["id"] == "common_date_clamped_per_product")

    consent = deepcopy(seed)
    consent["id"] = "consent_only_clause_confirms_unchanged_product_dates"
    consent["turns"][1]["extracted"]["turn_terms"] = json.dumps(
        [{"scope": "all", "decision": "accept"}]
    )
    fixtures["cases"].append(consent)

    aliases = deepcopy(seed)
    aliases["id"] = "product_names_resolve_to_bank_ids"
    # Keep both offers within their deadlines: isolate name resolution from
    # the separate, valid behavior of counteroffering an individual late date.
    for index in range(2):
        aliases["turns"][0]["expect"]["variables"][f"state.products.{index}.date"] = "2026-10-16"
    for response in aliases["responses"]:
        if response["operation"] == "create_promise":
            response["expect_body"]["date"] = "2026-10-16"
    turn = aliases["turns"][0]["extracted"]
    turn.update(turn_date="", turn_date_kind="none", turn_scope="")
    turn["turn_terms"] = json.dumps(
        [
            {
                "scope": name,
                "decision": "reject",
                "date": "2026-10-16",
                "date_kind": "exact",
                "date_words": "this Friday",
                "evidence": f"I will pay the {name} this Friday",
            }
            for name in ("credit card", "personal loan")
        ]
    )
    turn["turn_raw"] = (
        "I will pay the credit card this Friday and I will pay the personal loan this Friday."
    )
    turn["turn_change_evidence"] = turn["turn_raw"]
    fixtures["cases"].append(aliases)

    negated = deepcopy(aliases)
    negated["id"] = "negated_today_does_not_replace_positive_friday"
    negated["turns"][0]["extracted"].update(
        turn_raw="I cannot pay today, but I will pay both this Friday.",
        turn_date="2026-10-16",
        turn_date_kind="exact",
        turn_date_words="",
        turn_change_evidence="",
        turn_terms="[]",
        turn_scope="all",
    )
    fixtures["cases"].append(negated)

    wrong_year = deepcopy(seed)
    wrong_year["id"] = "spoken_month_day_uses_call_time_year"
    wrong_year["turns"][0]["extracted"].update(
        turn_date="2024-10-30",
        turn_date_words="thirtieth of October",
        turn_raw="I will pay all of them on the thirtieth of October.",
        turn_change_evidence="I will pay all of them on the thirtieth of October.",
    )
    fixtures["cases"].append(wrong_year)

    cardinal = deepcopy(seed)
    cardinal["id"] = "spoken_cardinal_day_after_rejected_today"
    cardinal["turns"][0]["extracted"].update(
        turn_raw="I cannot pay today. I want to pay both on thirty October.",
        turn_decision="reject",
        turn_topics="paid",
        turn_scope="all",
        turn_date="2024-10-30",
        turn_date_kind="exact",
        turn_date_words="thirty October",
        turn_change_evidence="I want to pay both on thirty October",
        turn_terms=json.dumps(
            [
                {
                    "scope": "all",
                    "decision": "accept",
                    "date": "2024-10-30",
                    "date_words": "thirty October",
                    "evidence": "I want to pay both on thirty October",
                }
            ]
        ),
    )
    fixtures["cases"].append(cardinal)

    if args.stale_call:
        transcript = json.loads(args.stale_call.read_text())["call"]["messages"]
        snapshots = []
        for message in transcript:
            if message.get("role") != "system":
                continue
            try:
                event = json.loads(message["message"])
            except (ValueError, TypeError):
                continue
            table = event.get("variable_table", {})
            if (
                event.get("event") == "route_evaluated"
                and event.get("from_node") == "dialogue"
                and table.get("turn_raw") == "I am not paying it."
                and table.get("turn_date")
            ):
                snapshots.append(table)
        if not snapshots:
            raise ValueError("No recorded refusal with a merged stale date found")
        snapshot = snapshots[-1]
        variables = {
            key: value
            for key, value in snapshot.items()
            if key.startswith("turn_")
            or key in {"state", "phase", "now", "message", "pending_offer", "product_catalog"}
        }
        variables["customer_id"] = "offline-b"
        state = json.loads(variables["state"])
        active = state["active"]
        fixtures["cases"].append(
            {
                "id": "live_refusal_ignores_merged_stale_date",
                "start_node": "dialogue",
                "variables": variables,
                "responses": [],
                "turns": [
                    {
                        "extracted": {k: v for k, v in variables.items() if k.startswith("turn_")},
                        "expect": {
                            "variables": {
                                "state.phase": "confirm",
                                f"state.products.{active}.status": "excluded",
                                f"state.products.{active}.reason": "refused",
                                "state.products.0.date": state["products"][0]["date"],
                            }
                        },
                    }
                ],
            }
        )
        fixtures["live_source_sha256"] = hashlib.sha256(args.stale_call.read_bytes()).hexdigest()

    fixtures["name"] = "Brief B live-inspired extraction regressions"
    fixtures["fixture_workflow_sha256"] = hashlib.sha256(args.workflow.read_bytes()).hexdigest()
    (args.out / "suite.json").write_text(json.dumps(fixtures, indent=2))
    result = run_suite(workflow, fixtures)
    (args.out / "results.json").write_text(json.dumps(result, indent=2))
    failed = {row["id"] for row in result["rows"] if not row["passed"]}
    details = []
    for case in fixtures["cases"]:
        if case["id"] not in failed:
            continue
        replay = Replay(workflow, case["variables"], case.get("responses", []))
        replay.node = case.get("start_node", replay.node)
        try:
            replay.advance()
            for step in case["turns"]:
                replay.turn(step["extracted"])
                check(replay, step.get("expect", {}))
        except (ValueError, KeyError, TypeError, IndexError):
            pass
        details.append(
            {
                "id": case["id"],
                "state": json.loads(replay.variables["state"]),
                "message": replay.variables.get("message"),
            }
        )
    (args.out / "failure-states.json").write_text(json.dumps(details, indent=2))
    print(
        json.dumps({"passed": result["passed"], "total": result["total"], "failed": sorted(failed)})
    )


if __name__ == "__main__":
    main()
