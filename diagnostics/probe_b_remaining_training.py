"""Reproduce known training failures locally without changing official scores."""

from __future__ import annotations

import argparse
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
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    workflow = json.loads(args.workflow.read_text())
    code = workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]
    scenarios = [
        ("b-ambiguous-amount-after-scoped-date", [[220, 213, 75]], False),
        ("b-incomplete-multi-product-correction", [[75, 213, 75], [220, 213, 75]], False),
        ("b-insistence-after-human-offer", None, True),
    ]

    def probe() -> dict:
        rows = []
        for name, expected_amounts, transfer in scenarios:
            fixture = ROOT / "tests/fixtures" / f"{name}.json"
            values = json.loads(fixture.read_text())
            result = execute_python(code, {"vars": values})["vars"]
            state = json.loads(result["state"])
            amounts = [p["amount"] for p in state["products"]]
            route = result["route"]
            passed = (
                route == "transfer_to_agent"
                if transfer
                else expected_amounts is not None
                and amounts in expected_amounts
                and route != "create_promise"
                and not any(p.get("confirmed") for p in state["products"])
            )
            rows.append(
                {
                    "id": name,
                    "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                    "pass": passed,
                    "expected_amounts": expected_amounts,
                    "expected_transfer": transfer,
                    "actual_amounts": amounts,
                    "route": route,
                    "phase": state["phase"],
                    "message": result.get("message"),
                }
            )
        report = {
            "workflow": str(args.workflow),
            "workflow_sha256": hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            "network_calls": False,
            "official_score": False,
            "evidence_split": "train",
            "passed": sum(row["pass"] is True for row in rows),
            "total": len(rows),
            "rows": rows,
        }
        write_json(args.out / "probe.json", report)
        return report

    report = capture_sync(args.out, "offline_training_failure_probe", probe)
    print(json.dumps({"passed": report["passed"], "total": report["total"]}))


if __name__ == "__main__":
    main()
