"""Run established behavioral regressions against an arbitrary generated workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from freya_hackathon.builder import write_json
from freya_hackathon.recording import capture_sync

ROOT = Path(__file__).resolve().parents[1]
MODULES = {
    "A": [f"test_reference_a_{v}" for v in ("v2", "v3", "v4", "v5", "v6", "v9")],
    "B": [
        f"test_reference_b_{v}" for v in ("v2", "v3", "v4", "v5", "scope", "closure", "v9", "v10")
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    workflow_path = args.workflow.resolve()
    workflow = json.loads(workflow_path.read_text())
    brief = "B" if "decide" in workflow["nodes"] else "A"
    controller = "decide" if brief == "B" else "advance"
    args.out.mkdir(parents=True, exist_ok=False)
    modules = args.out / "generated_tests"
    modules.mkdir()
    for name in [*MODULES[brief], "test_reference_runtime_failure"]:
        source = ROOT / "tests" / f"{name}.py"
        wrapper = f"""import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("source_{name}", {str(source)!r})
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)
for name, value in vars(original).items():
    if name.startswith("test_") or name in ("variables", "mixed_variables"):
        globals()[name] = value
globals().pop("test_compaction_preserves_all_existing_code_ast", None)


@pytest.fixture
def reference():
    return Path({str(workflow_path)!r})


@pytest.fixture
def workflow(reference):
    return json.loads(reference.read_text())


@pytest.fixture
def code(workflow):
    function = workflow["nodes"][{controller!r}]["pre_actions"][0]["function"]
    properties = function["parameters"]["properties"]
    return properties["code"]["default"]
"""
        (modules / f"{name}.py").write_text(wrapper)

    def check() -> dict:
        junit = args.out / "junit.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(modules),
            f"--junitxml={junit}",
        ]
        with (args.out / "pytest.log").open("w") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
        tree = ET.parse(junit)
        tests = list(tree.iter("testcase"))
        failed = [
            case.attrib["name"]
            for case in tests
            if case.find("failure") is not None or case.find("error") is not None
        ]
        errors = [case.attrib["name"] for case in tests if case.find("error") is not None]
        assertions = [
            {"test": case.attrib["name"], "message": failure.attrib.get("message", "")[:800]}
            for case in tests
            if (failure := case.find("failure")) is not None
        ]
        skipped = sum(case.find("skipped") is not None for case in tests)
        report = {
            "workflow": str(workflow_path),
            "workflow_sha256": hashlib.sha256(workflow_path.read_bytes()).hexdigest(),
            "brief": brief,
            "network_calls": False,
            "official_score": False,
            "total": len(tests),
            "passed": len(tests) - len(failed) - skipped,
            "failed": failed,
            "errors": errors,
            "assertion_failures": assertions,
            "skipped": skipped,
            "exit_code": completed.returncode,
        }
        write_json(args.out / "report.json", report)
        return report

    report = capture_sync(args.out, "generated_workflow_regressions", check)
    print(json.dumps(report))
    if report["exit_code"]:
        raise SystemExit(report["exit_code"])


if __name__ == "__main__":
    main()
