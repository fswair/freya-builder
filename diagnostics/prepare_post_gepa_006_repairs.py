"""Prepare explicit manual repairs outside the frozen GEPA search and selection."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from probe_a_complete_restatement import proposed_correction

from freya_hackathon.builder import write_json
from freya_hackathon.recording import capture_sync

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "runs/gepa-training-first-006"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def prepare() -> dict:
        rows = []
        for brief, candidate in (
            ("A", "003-A-03fe743c49c0"),
            ("B", "004-B-03fe743c49c0"),
        ):
            source = STUDY / "candidates" / candidate / "workflow.json"
            original = json.loads(source.read_text())
            workflow = copy.deepcopy(original)
            changes = []
            if brief == "A":
                target = workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"][
                    "properties"
                ]["code"]
                before = target["default"]
                target["default"] = proposed_correction(before)
                changes.append(
                    {
                        "path": "nodes.advance.pre_actions.0.code",
                        "reason": (
                            "Ground a direct positive amount restatement after date stripping."
                        ),
                        "before_sha256": hashlib.sha256(before.encode()).hexdigest(),
                        "after_sha256": hashlib.sha256(target["default"].encode()).hexdigest(),
                    }
                )
            else:
                reference = ROOT / "benchmarks/development-references-v10/multi-product.json"
                trusted = json.loads(reference.read_text())
                for node in ("runtime_recovery", "runtime_unavailable"):
                    workflow["nodes"][node] = copy.deepcopy(trusted["nodes"][node])
                    changes.append({"path": f"nodes.{node}", "restored_from": str(reference)})
                workflow["nodes"]["decide"]["transitions"][0]["condition"] = copy.deepcopy(
                    trusted["nodes"]["decide"]["transitions"][0]["condition"]
                )
                changes.append(
                    {
                        "path": "nodes.decide.transitions.0.condition",
                        "restored_from": str(reference),
                        "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
                    }
                )
                code_field = workflow["nodes"]["decide"]["pre_actions"][0]["function"][
                    "parameters"
                ]["properties"]["code"]
                before = "if attempt_day == today:"
                after = "if attempt_day <= today:"
                assert code_field["default"].count(before) == 1
                assert (
                    after
                    in trusted["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"][
                        "properties"
                    ]["code"]["default"]
                )
                code_field["default"] = code_field["default"].replace(before, after)
                changes.append(
                    {
                        "path": "nodes.decide.pre_actions.0.code",
                        "before": before,
                        "after": after,
                        "reason": "Restore source's prohibition on past-date proposals.",
                        "restored_from": str(reference),
                    }
                )
            output = args.out / brief
            output.mkdir()
            write_json(output / "workflow.json", workflow)
            row = {
                "brief": brief,
                "source": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "workflow_sha256": hashlib.sha256(
                    (output / "workflow.json").read_bytes()
                ).hexdigest(),
                "changes": changes,
                "fresh_generation": False,
                "author": "assistant deterministic diagnostic repair",
                "scope": "Training/local evidence only; outside frozen GEPA selection.",
                "native_score": None,
                "promoted": False,
            }
            write_json(output / "provenance.json", row)
            rows.append(row)
        report = {"frozen_study_modified": False, "candidates": rows}
        write_json(args.out / "report.json", report)
        return report

    capture_sync(args.out, "manual_post_gepa_repairs", prepare)
    print(args.out)


if __name__ == "__main__":
    main()
