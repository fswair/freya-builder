"""Validate a candidate locally and on Freya without saving or simulating it."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import httpx
from training_first_gepa import ReadResilientPractice

from freya_hackathon.builder import ROOT, write_json
from freya_hackathon.policy import Policy
from freya_hackathon.recording import capture_sync, redact, secrets_to_redact
from freya_hackathon.workflow import WorkflowBuild, validate_workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent", type=Path)
    parser.add_argument("workflow", type=Path)
    parser.add_argument(
        "--tool-api", type=Path, default=ROOT / "vendor/freya-v3/tools/openapi.json"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=90)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    def validate():
        workflow = json.loads(args.workflow.read_text())
        local = validate_workflow(
            WorkflowBuild(
                workflow=workflow,
                rule_nodes=json.loads((args.parent / "rule-nodes.json").read_text()),
            ),
            Policy.model_validate_json((args.parent / "policy.json").read_text()),
            json.loads(args.tool_api.read_text()),
        )
        write_json(args.out / "local-validation.json", local)
        practice = ReadResilientPractice(args.out, args.seconds)
        try:
            native = practice.freya.validate_workflow("reference-candidate-dry-run", workflow)
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json()
            except ValueError:
                detail = exc.response.text[:8000]
            native = {
                "valid": False,
                "http_status": exc.response.status_code,
                "error": redact(detail, secrets_to_redact()),
            }
        finally:
            practice.client.close()
        write_json(args.out / "freya-validation.json", native)
        report = {
            "workflow": str(args.workflow),
            "workflow_sha256": hashlib.sha256(args.workflow.read_bytes()).hexdigest(),
            "local_status": local["status"],
            "freya_valid": native.get("valid") is True,
            "workflow_saved": False,
            "new_simulation": False,
            "real_call": False,
            "behavioral_quality_claim": False,
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "reference_validation_only", validate)
    print(json.dumps(report))
    if not report["freya_valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
