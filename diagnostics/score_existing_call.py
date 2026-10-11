"""Read a completed synthetic call's current bank ledger without waiting for the whole iteration."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from freya_hackathon.builder import utc_now, write_json
from freya_hackathon.practice import Practice
from freya_hackathon.recording import capture_sync, redact, secrets_to_redact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("call", type=Path)
    parser.add_argument("cases", type=Path)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    response = json.loads(args.call.read_text())
    call = response.get("call", response)
    cases = json.loads(args.cases.read_text())
    selected = [case for case in cases if case["id"] == args.case_id]
    if len(selected) != 1:
        raise ValueError("Expected exactly one matching practice case")
    customer_id = None
    for message in call.get("messages", []):
        if message.get("role") != "system":
            continue
        try:
            event = json.loads(message["message"])
        except (ValueError, TypeError):
            continue
        value = event.get("variable_table", {}).get("customer_id")
        if value:
            customer_id = value.get("value") if isinstance(value, dict) else value
            break
    if not isinstance(customer_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", customer_id):
        raise ValueError("No safe synthetic customer identifier in the recorded call")
    if not call.get("endedAt"):
        raise ValueError("Only ended call evidence can be scored by this helper")
    args.out.mkdir(parents=True, exist_ok=False)

    def inspect() -> dict:
        practice = Practice(args.out, 30)
        report = {
            "case_id": args.case_id,
            "call_id": call.get("id"),
            "captured_at": utc_now(),
            "new_call": False,
            "new_simulation": False,
            "bank_method": "GET",
            "scope": "Provisional call evidence; not a final score or promotion gate.",
            "limit": "The final collector must reread the ledger; this snapshot can become stale.",
            "official_outcome_overridden": False,
            "call_judge_success": call.get("successCriteriaResult", {}).get("success"),
        }
        try:
            ledger = practice.bank("/v1/practice/ledger/" + customer_id)
            ledger = redact(ledger, secrets_to_redact())
            write_json(practice.output / f"ledger-{customer_id}.json", ledger)
            score = practice.cases.score_ledger(selected[0], ledger)
            report.update(
                read_succeeded=True, ledger_passed=not score["failed"], ledger_score=score
            )
        except Exception as exc:
            report.update(read_succeeded=False, error_type=type(exc).__name__)
        finally:
            practice.client.close()
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "existing_call_ledger_read", inspect)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
