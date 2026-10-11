"""Replay a training-only amount correction against unchanged and proposed code."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync

ROOT = Path(__file__).resolve().parents[1]
CALL = ROOT / (
    "runs/gepa-training-first-006/evaluations/001-train-A/repeat-1/practice/"
    "call-cb0deb3a-8d87-4962-ad6a-084cb23373c1.json"
)


def proposed_correction(code: str) -> str:
    """Recognize an explicit positive amount restatement after date stripping."""
    marker = "or bare_amount or product_amount or (not date_hint"
    assert code.count(marker) == 1
    correction = (
        "or bool(re.search(r'^(?:(?:no|sorry|actually)[,\\s]+)*"
        "(?:i|we)\\s+(?:mean|meant)\\s+"
        "(?:the\\s+)?(?:full\\s+|whole\\s+)?(?:amount\\s+(?:of\\s+)?)?"
        "[$£€]?\\s*\\d', cleaned.strip(), re.I)) "
    )
    return code.replace(marker, correction + marker)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    keys = set(json.loads((ROOT / "tests/fixtures/a-dash-date-correction.json").read_text()))
    messages = json.loads(CALL.read_text())["call"]["messages"]
    event = json.loads(messages[139]["message"])
    assert event["event"] == "route_evaluated" and event["target"] == "advance"
    snapshot = {k: v for k, v in event["variable_table"].items() if k in keys}
    write_json(args.out / "training-snapshot.json", snapshot)

    def probe() -> dict:
        rows = []
        for path in args.workflow:
            workflow = json.loads(path.read_text())
            code = workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"][
                "properties"
            ]["code"]["default"]
            for variant, candidate in (
                ("unchanged", code),
                ("proposed_correction", proposed_correction(code)),
            ):
                result = execute_python(candidate, {"vars": snapshot})["vars"]
                state = json.loads(result["call_state"])
                product = state["products"][state["product_id"]]
                rows.append(
                    {
                        "workflow": str(path),
                        "workflow_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "variant": variant,
                        "code_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
                        "next": result["next"],
                        "stage": state["stage"],
                        "instruction": result["instruction"],
                        "amount": product["amount"],
                        "date": product["date"],
                        "terms_unresolved": product.get("terms_unresolved"),
                        "pass": result["next"] == "confirm_speak"
                        and state["stage"] == "confirm"
                        and product["amount"] == 420
                        and product["date"] == "2026-10-14"
                        and not product.get("terms_unresolved"),
                    }
                )
        report = {
            "scope": "Advisory training replay; no native score or promotion.",
            "source_call": str(CALL),
            "source_call_sha256": hashlib.sha256(CALL.read_bytes()).hexdigest(),
            "message_index": 139,
            "network_calls": False,
            "source_workflows_modified": False,
            "results": rows,
        }
        write_json(args.out / "report.json", report)
        return report

    result = capture_sync(args.out, "offline_complete_restatement", probe)
    print(
        json.dumps(
            [
                {k: row[k] for k in ("workflow", "variant", "pass", "next")}
                for row in result["results"]
            ]
        )
    )


if __name__ == "__main__":
    main()
