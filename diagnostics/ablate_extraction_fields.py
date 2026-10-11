"""Measure field dependencies on saved public/train B turns without network calls."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.offline import execute_python
from freya_hackathon.recording import capture_sync


def inputs(runs):
    samples, sources = [], []
    for run in runs:
        cases = json.loads((run / "practice/cases.json").read_text())
        if any(
            row["brief"] != "B" or not row["id"].startswith(("PUB-", "DEV-TR-")) for row in cases
        ):
            raise ValueError("Only explicit B public/train runs are allowed")
        results = json.loads((run / "practice/results.json").read_text())
        for row in results["rows"]:
            path = run / "practice" / f"call-{row['call_id']}.json"
            call = json.loads(path.read_text())["call"]
            sources.append(
                {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
            for message in call["messages"]:
                try:
                    event = json.loads(message["message"])
                except (ValueError, TypeError):
                    continue
                if event.get("event") != "route_evaluated" or message.get("nodeId") != "dialogue":
                    continue
                values = event.get("variable_table", {})
                if not values.get("turn_raw") or not values.get("state"):
                    continue
                state = json.loads(values["state"])
                samples.append(
                    {
                        "case": row["id"],
                        "call_id": row["call_id"],
                        "seconds": message["secondsFromStart"],
                        "phase": state.get("phase"),
                        "values": values,
                    }
                )
    return samples, sources


def replay(code, sample, removed=()):
    values = copy.deepcopy(sample["values"])
    for field in removed:
        values[field] = ""
    result = execute_python(code, {"vars": values})["vars"]
    return {
        "state": json.loads(result["state"]),
        **{
            key: value
            for key, value in result.items()
            if key in ("route", "message", "pending_offer", "farewell", "outcome", "reason")
            or key.startswith("request_")
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def measure():
        workflow = json.loads(args.workflow.read_text())
        code = workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"][
            "properties"
        ]["code"]["default"]
        fields = [entry["name"] for entry in workflow["nodes"]["dialogue"]["extraction_schema"]]
        samples, sources = inputs(args.runs)
        baseline = [replay(code, sample) for sample in samples]
        rows = []
        for field in fields:
            relevant = [i for i, sample in enumerate(samples) if sample["values"].get(field)]
            differences = [
                {key: samples[i][key] for key in ("case", "call_id", "seconds", "phase")}
                for i in relevant
                if replay(code, samples[i], (field,)) != baseline[i]
            ]
            rows.append(
                {
                    "removed": field,
                    "populated_samples": len(relevant),
                    "changed_outputs": len(differences),
                    "examples": differences[:8],
                }
            )
        zero_change = [row["removed"] for row in rows if row["changed_outputs"] == 0]
        joint = [
            {key: sample[key] for key in ("case", "call_id", "seconds", "phase")}
            for i, sample in enumerate(samples)
            if replay(code, sample, zero_change) != baseline[i]
        ]
        report = {
            "workflow": str(args.workflow),
            "workflow_sha256": hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            "samples": len(samples),
            "fields": len(fields),
            "rows": rows,
            "individually_zero_change": zero_change,
            "joint_removal_changes": len(joint),
            "joint_examples": joint[:8],
            "sources": sources,
            "network_calls": False,
            "native_latency_measurement": False,
            "promoted": False,
            "limits": "Existing extracted turns only. No field pruning or schema change is "
            "applied. Output equality does not establish future extraction quality or speed.",
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "extraction_field_ablation", measure)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "samples",
                    "fields",
                    "individually_zero_change",
                    "joint_removal_changes",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
