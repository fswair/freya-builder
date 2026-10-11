"""Replay public/train correction fragments and complete-request controls."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from probe_b_native_repairs import ROOT, replay

from freya_hackathon.builder import write_json
from freya_hackathon.recording import capture_sync

CALL = (
    ROOT / "runs/v13-b-targeted-native-001/practice/call-fb634d07-47ba-4e3b-af7a-962976601e54.json"
)
FRAGMENTS = (
    "Keep the first two. Change only",
    "Change only the car loan to 15",
    "Keep the first two. Change only the car loan to",
    "Keep the first",
    "Keep the first one. Change the personal loan",
    "Keep the first two. Change only the car loan",
)


def seeds() -> dict:
    found = {}
    for message in json.loads(CALL.read_text())["call"]["messages"]:
        try:
            event = json.loads(message["message"])
        except (ValueError, TypeError):
            continue
        if event.get("event") != "route_evaluated" or message.get("nodeId") != "dialogue":
            continue
        values = event.get("variable_table", {})
        raw = values.get("turn_raw")
        if raw in FRAGMENTS and raw not in found:
            found[raw] = (values, message["secondsFromStart"])
    assert set(found) == set(FRAGMENTS)
    return found


def unchanged(values, result):
    before = json.loads(values["state"])["products"]
    fields = ("status", "amount", "date", "confirmed")
    return result["route"] == "dialogue" and all(
        all(original.get(key) == actual.get(key) for key in fields)
        for original, actual in zip(before, result["products"], strict=True)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def check():
        workflows = {
            key: json.loads(path.read_text())
            for key, path in (("baseline", args.baseline), ("candidate", args.candidate))
        }
        rows = []
        native = seeds()
        for raw, (values, seconds) in native.items():
            results = {key: replay(workflow, values) for key, workflow in workflows.items()}
            rows.append(
                {
                    "speech": raw,
                    "native_input": True,
                    "seconds": seconds,
                    "baseline_pass": unchanged(values, results["baseline"]),
                    "candidate_pass": unchanged(values, results["candidate"]),
                    "results": results,
                }
            )
        template = native[FRAGMENTS[0]][0]
        for raw, expected_route in (
            ("I need lower monthly instalments.", "transfer_to_agent"),
            ("Restructuring please.", "transfer_to_agent"),
            ("I am not asking for restructuring. Keep the existing terms.", "dialogue"),
            ("Please change only the car loan to 15 October 2026.", "dialogue"),
        ):
            values = copy.deepcopy(template)
            values.update({key: "" for key in values if key.startswith("turn_")})
            values["turn_raw"] = raw
            values["turn_topics"] = "restructuring"  # noisy extractor control
            result = replay(workflows["candidate"], values)
            valid = result["route"] == expected_route
            if "15 October" in raw:
                valid = valid and result["products"][2]["date"] == "2026-10-15"
                valid = valid and result["products"][0]["date"] == "2026-10-16"
                valid = valid and result["products"][1]["date"] == "2026-10-23"
            rows.append(
                {"speech": raw, "native_input": False, "candidate_pass": valid, "result": result}
            )
        report = {
            "passed": sum(row["candidate_pass"] is True for row in rows),
            "total": len(rows),
            "rows": rows,
            "source_call": str(CALL.relative_to(ROOT)),
            "source_sha256": hashlib.sha256(CALL.read_bytes()).hexdigest(),
            "workflow_sha256": hashlib.sha256(args.candidate.read_bytes()).hexdigest(),
            "native_simulation": False,
            "promoted": False,
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "b_interrupted_correction_replay", check)
    print(json.dumps({"passed": report["passed"], "total": report["total"]}))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
