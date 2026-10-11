"""Summarize persisted study metadata without API calls, replay, or case payload reads."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


class Summary:
    def __init__(self, root: Path):
        self.root = root
        self.errors: list[dict[str, str]] = []

    def relative(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    def read(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            self.errors.append({"path": self.relative(path), "error": type(exc).__name__})
            return {}
        return value if isinstance(value, dict) else {}

    def record(self, path: Path) -> dict[str, Any]:
        result: dict[str, Any] = {"path": self.relative(path), "status": "unavailable"}
        # Only these flat metadata scalars are needed; no YAML dependency or
        # case/artifact payload parsing is necessary for this diagnostic.
        try:
            text = (path / "summary.yaml").read_text()
        except FileNotFoundError:
            return result
        except OSError as exc:
            self.errors.append({"path": self.relative(path), "error": type(exc).__name__})
            return result
        section = ""
        for line in text.splitlines():
            if match := re.fullmatch(r"(summary|runs):", line):
                section = match[1]
            elif line and not line.startswith((" ", "#")):
                section = ""
            elif match := re.fullmatch(r"  ([a-z_]+): ([^\n]+)", line):
                key, value = match.groups()
                if section == "summary" and key in {"status", "partial"}:
                    result[key] = value.strip("\"'")
                elif section == "runs" and value.isdecimal():
                    result.setdefault("runs", {})[key] = int(value)
        return result

    def repeat(self, path: Path) -> dict[str, Any]:
        practice = path / "practice"
        report = self.read(practice / "results.json")
        health = self.read(practice / "runtime-health.json")
        iteration = self.read(practice / "iteration.json")
        # Iteration metadata includes persona text; inspect only numeric retry
        # counters. Never open cases.json, call transcripts, or data/ directories.
        attempts = [
            row["attempts"]
            for row in iteration.get("runs", [])
            if isinstance(row, dict) and isinstance(row.get("attempts"), int)
        ]
        affected = health.get("infrastructure_affected_cases")
        state = self.read(practice / "simulation-state.json")
        state = state.get("simulation", state)
        state = state if isinstance(state, dict) else {}
        result = {
            "path": self.relative(path),
            "status": report.get("status", state.get("status", "unavailable")),
            "passed": report.get("passed"),
            "total": report.get("total"),
            "seconds": report.get("seconds"),
            "simulation_id": report.get("simulation_id")
            or self.read(practice / "simulation.json").get("simulation_id"),
            "infrastructure_affected_count": len(affected) if isinstance(affected, list) else None,
        }
        passed, total = report.get("passed"), report.get("total")
        if isinstance(passed, (int, float)) and isinstance(total, (int, float)) and total > 0:
            result["score"] = passed / total
        if attempts:
            result["retry_counters"] = {
                "runs_with_counters": len(attempts),
                "raw_attempts_total": sum(attempts),
                "runs_with_nonzero_attempts": sum(value > 0 for value in attempts),
                "max_attempts_used": max(attempts),
            }
        return result

    def evaluation(self, path: Path) -> dict[str, Any]:
        match = re.fullmatch(r"\d+-(train|validation)-(.+)", path.name)
        report = self.read(path / "evaluation.json")
        repeats = [self.repeat(p) for p in sorted(path.glob("repeat-*")) if p.is_dir()]
        build = report.get("build")
        if build is None and repeats:
            build = self.read(self.root / repeats[0]["path"] / "parent.json").get("build")
        return {
            "path": self.relative(path),
            "split": match[1] if match else None,
            "brief": match[2] if match else None,
            "build": build,
            "repetitions": report.get("repetitions"),
            "score": report.get("score"),
            "passed": report.get("passed"),
            "total": report.get("total"),
            "repeat_results": repeats,
        }

    def run(self) -> dict[str, Any]:
        selection = self.read(self.root / "selection.json")
        result = self.read(self.root / "gepa-result.json")
        aborted = self.read(self.root / "aborted.json")
        records = {
            kind: [
                self.record(p)
                for p in sorted(self.root.glob(pattern))
                if p.is_dir()
                and not any("-holdout-" in part for part in p.relative_to(self.root).parts)
            ]
            for kind, pattern in {
                "optimization": "autobench/optimization-*",
                "builder": "candidates/*/autobench/builder-*",
                "practice": "evaluations/*/repeat-*/autobench/practice-*",
                "practice_cases": "evaluations/*/repeat-*/autobench/cases-*",
                "collect": "evaluations/*/repeat-*/autobench/collect-*",
            }.items()
        }
        return {
            "study_dir": str(self.root),
            "exists": self.root.is_dir(),
            "selection": {
                key: selection[key]
                for key in (
                    "status",
                    "evidence_scope",
                    "validation_score",
                    "guidance_sha256",
                    "selected_builds",
                    "metric_calls",
                    "builds",
                    "source_fingerprint",
                    "holdout_evaluated",
                    "production_promoted",
                )
                if key in selection
            },
            "gepa_result": {
                key: result[key]
                for key in (
                    "best_score",
                    "best_candidate_index",
                    "validation_scores",
                    "total_metric_calls",
                    "num_full_val_evals",
                    "stop_reason",
                )
                if key in result
            },
            "aborted": {key: aborted[key] for key in ("reason", "time") if key in aborted},
            "evaluations": [
                self.evaluation(p)
                for p in sorted((self.root / "evaluations").glob("*"))
                if p.is_dir() and re.fullmatch(r"\d+-(train|validation)-.+", p.name)
            ],
            "records": records,
            "read_errors": self.errors,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(Summary(args.study_dir.resolve()).run(), separators=(",", ":")))


if __name__ == "__main__":
    main()
