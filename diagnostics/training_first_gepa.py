"""Bounded train-only GEPA search followed by sealed, repeated validation selection.

GEPA's internal validation is explicitly training data here. Its score cannot select
a submission candidate. Only the separate post-search validation can select for review.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml
from autobench import replay_experiment
from pydantic_gepa import Component, DerivedValueInjection, GEPAConfig, MetricResult, Optimization
from pydantic_gepa.configuration import BudgetConfig, ReflectionConfig, RunConfig

from freya_hackathon.builder import utc_now, write_json
from freya_hackathon.optimization import (
    CandidateRejected,
    Study,
    repeated_score,
    source_fingerprint,
    validate_guidance,
    workspace_lock,
)
from freya_hackathon.optimization_data import StudyConfig, fingerprint, load_development, registry
from freya_hackathon.practice import Practice
from freya_hackathon.recording import capture_sync, emit


class ReadResilientPractice(Practice):
    """Retry transient GET failures only; never repeat a submission or bank write."""

    def api(self, method, path, body=None, **params):
        for attempt in range(3):
            try:
                return super().api(method, path, body, **params)
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                transient = isinstance(exc, httpx.TransportError) or exc.response.status_code in {
                    429,
                    500,
                    502,
                    503,
                    504,
                }
                if method.upper() != "GET" or not transient or attempt == 2:
                    raise
                self.trace(
                    "practice_read_retry", path=path, attempt=attempt + 1, error=type(exc).__name__
                )
                time.sleep(min(2, self.remaining()))
        raise RuntimeError("Unreachable read retry state")


def local_behavior_gate(build: Path) -> dict:
    """Reject unsafe behavior before deployment; harness errors abort the study."""
    root = build / "local-gate"
    root.mkdir(parents=True, exist_ok=False)
    scripts = Path(__file__).parent
    for script, destination in [
        ("check_generated_regressions.py", "regressions"),
        ("probe_generated_consent.py", "consent"),
    ]:
        with (root / f"{destination}.log").open("w") as log:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(scripts / script),
                    str(build / "workflow.json"),
                    str(root / destination),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=120,
                check=False,
            )
            if completed.returncode not in ({0, 1} if destination == "regressions" else {0}):
                raise RuntimeError(f"Local {destination} harness execution failed")
    try:
        regression = json.loads((root / "regressions/report.json").read_text())
        consent = json.loads((root / "consent/probe.json").read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError("Local behavior gate evidence is missing or invalid") from exc
    if regression.get("errors") or regression["skipped"] or not regression["total"]:
        raise RuntimeError("Local regression harness produced errors or incomplete evidence")
    result = {
        "pass": not regression["failed"] and consent["passed"] == consent["total"] == 9,
        "regressions": {"passed": regression["passed"], "total": regression["total"]},
        "assertion_failures": regression.get("assertion_failures", []),
        "consent_failures": [row for row in consent["rows"] if not row["pass"]],
        "scope": "offline training regressions; no validation or holdout cases",
    }
    write_json(root / "result.json", result)
    return result


def summarize_training_failure(row: dict, messages: list[dict]) -> dict:
    """Keep both the trigger and outcome of long calls within the reflection budget."""
    dialogue = [m for m in messages if m.get("role") in {"assistant", "user"}]
    indices = sorted(
        set(range(min(10, len(dialogue)))) | set(range(max(0, len(dialogue) - 10), len(dialogue)))
    )
    return {
        "id": row["id"],
        "judge_reason": str(row.get("simulation_reason") or "")[:1000],
        "dialogue_turns": len(dialogue),
        "omitted_turns": len(dialogue) - len(indices),
        "dialogue": [
            {
                "turn": index,
                "role": dialogue[index]["role"],
                "text": str(dialogue[index].get("message", ""))[:240],
            }
            for index in indices
        ],
    }


def enrich_training_feedback(output: Path, result: dict) -> None:
    if "path" not in result:
        return
    destination = output / result["path"]
    evidence = []
    for report_path in sorted(destination.glob("repeat-*/practice/results.json")):
        report = json.loads(report_path.read_text())
        for row in report.get("rows", []):
            if row.get("pass") is True or len(evidence) >= 8:
                continue
            call_path = report_path.parent / f"call-{row.get('call_id')}.json"
            if not call_path.exists():
                continue
            call = json.loads(call_path.read_text())
            messages = (call.get("call") or call).get("messages", [])
            evidence.append(summarize_training_failure(row, messages))
    if evidence:
        result["evidence"] = evidence
        write_json(destination / "training-feedback.json", result)


def choose_validation_candidate(scores: list[dict[str, float]]) -> int:
    """Require a strict mean gain and no brief regression; ties retain the initial one."""
    if not scores or not scores[0]:
        raise ValueError("Validation scores are required")
    keys = set(scores[0])
    for row in scores:
        if set(row) != keys or any(
            type(v) not in (int, float) or not 0 <= v <= 1 for v in row.values()
        ):
            raise ValueError("Invalid or incomplete validation scores")
    chosen = 0
    for index, row in enumerate(scores[1:], 1):
        if all(row[key] >= scores[chosen][key] for key in keys) and sum(row.values()) > sum(
            scores[chosen].values()
        ):
            chosen = index
    return chosen


class TrainingFirstStudy(Study):
    def __init__(self, *args, local_gate=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.phase = "search"
        self.training_cache: dict[tuple, dict] = {}
        self.cache_hits = 0
        self.local_gate = local_gate
        self.gated_builds = {}

    def build(self, brief: str, guidance: str) -> Path:
        built = super().build(brief, guidance)
        if self.local_gate is not None:
            digest = hashlib.sha256((built / "workflow.json").read_bytes()).hexdigest()
            if built not in self.gated_builds:
                self.gated_builds[built] = (digest, self.local_gate(built))
            prior_digest, gate = self.gated_builds[built]
            if digest != prior_digest:
                raise RuntimeError("Candidate workflow changed after its local behavior gate")
            if not gate["pass"]:
                raise CandidateRejected("Local behavioral regression: " + json.dumps(gate))
        return built

    def evaluate(self, batch):
        if self.phase == "search" and batch.split != "train":
            raise ValueError("Search must never evaluate validation or holdout cases")
        key = (fingerprint(self.context.require()), batch.brief, fingerprint(batch.cases))
        if batch.split == "train" and key in self.training_cache:
            if self.aborted:
                raise RuntimeError("Study aborted")
            self.cache_hits += 1
            result = copy.deepcopy(self.training_cache[key])
            emit("training_evidence_reused", brief=batch.brief, evidence=result.get("path"))
            return result
        result = super().evaluate(batch)
        if batch.split == "train":
            try:
                enrich_training_feedback(self.output, result)
            except Exception as exc:
                self.aborted = type(exc).__name__
                write_json(
                    self.output / "aborted.json", {"reason": self.aborted, "time": utc_now()}
                )
                raise
            self.training_cache[key] = copy.deepcopy(result)
        return result

    def optimize(self, proposer=None):
        initial = validate_guidance(self.config.guidance.read_text())
        proposed = []

        def propose(candidate, evidence, components):
            if self.phase != "search":
                raise RuntimeError("Reflection is forbidden after validation begins")
            updated = (proposer or self.propose)(candidate, evidence, components)
            guidance = validate_guidance(updated["guidance"])
            if guidance != initial and guidance not in proposed:
                proposed.append(guidance)
            return updated

        optimization = Optimization.from_examples(
            examples=self.examples("train"),
            val_examples=self.examples("train"),
            task=self.evaluate,
            score=lambda ctx: MetricResult(
                score=ctx.output["score"],
                role="objective",
                semantic_type="quality.correctness",
                direction="maximize",
                feedback=json.dumps(ctx.output, ensure_ascii=False),
            ),
            components=[Component(name="guidance", initial_text=initial, kind="instructions")],
            injections=[
                DerivedValueInjection(
                    component="guidance",
                    context=self.context,
                    derive_value=lambda candidate: candidate["guidance"],
                    required_components=("guidance",),
                )
            ],
            max_concurrency=1,
            dataset_name="freya-training-only-guidance-search",
        )
        result = optimization.optimize(
            config=GEPAConfig(
                reflection=ReflectionConfig(
                    proposer=propose,
                    minibatch_size=len(self.config.briefs),
                    skip_perfect_score=False,
                ),
                budget=BudgetConfig(
                    max_metric_calls=self.config.metric_calls, stop=(self.stopped,)
                ),
                run=RunConfig(directory=self.output / "optimizer", seed=0, raise_on_exception=True),
            )
        )
        if self.aborted:
            raise RuntimeError("Training search aborted; no candidate was selected")
        write_json(self.output / "training-gepa-result.json", result.stable_dump())
        self.phase = "validation"
        guidances = [initial, *proposed]
        if len(guidances) > self.config.max_reflections + 1:
            raise RuntimeError("Post-search candidate budget exceeded")
        write_json(
            self.output / "validation-plan.json",
            {
                "started_at": utc_now(),
                "reflection_closed": True,
                "guidance_hashes": [fingerprint(g) for g in guidances],
                "repetitions": self.config.validation_repetitions,
                "maximum_validation_batches": len(guidances) * len(self.config.briefs),
                "maximum_validation_simulations": len(guidances)
                * len(self.config.briefs)
                * self.config.validation_repetitions,
                "tie_rule": "retain initial; strict gain without per-brief regression required",
                "holdout_opened": False,
            },
        )
        validations: list[dict] = []
        for guidance in guidances:
            with self.context.use(guidance):
                reports = {
                    example.inputs.brief: self.evaluate(example.inputs)
                    for example in self.examples("validation")
                }
            validations.append({"guidance_sha256": fingerprint(guidance), "reports": reports})
            write_json(self.output / "validation-progress.json", validations)
        if self.aborted:
            raise RuntimeError("Validation aborted; no candidate was selected")
        scores = [
            {brief: report["score"] for brief, report in v["reports"].items()} for v in validations
        ]
        chosen = choose_validation_candidate(scores)
        guidance = guidances[chosen]
        builds = {}
        for brief in self.config.briefs:
            built = self.builds.get((brief, fingerprint(guidance)))
            if built is None or not (built / "workflow.json").is_file():
                raise RuntimeError("Selected guidance has no validated build for every brief")
            if self.local_gate is not None and (
                built not in self.gated_builds or not self.gated_builds[built][1]["pass"]
            ):
                raise RuntimeError(
                    "Selected guidance still fails a local behavior gate; no selection"
                )
            builds[brief] = {
                "path": str(built.relative_to(self.output)),
                "workflow_sha256": hashlib.sha256(
                    (built / "workflow.json").read_bytes()
                ).hexdigest(),
            }
        selection = {
            "status": "selected_for_review",
            "evidence_scope": self.evidence_scope,
            "selection_method": "repeated validation; no brief regression; initial wins ties",
            "validation_score": sum(scores[chosen].values()) / len(scores[chosen]),
            "validation_by_brief": scores[chosen],
            "selected_index": chosen,
            "training_gepa_best_score": result.best_score,
            "metric_calls": result.total_metric_calls,
            "metric_call_scope": "GEPA training search only",
            "training_cache_hits": self.cache_hits,
            "validation_batches": len(guidances) * len(self.config.briefs),
            "guidance_sha256": fingerprint(guidance),
            "selected_builds": builds,
            "builds": self.build_count,
            "source_fingerprint": source_fingerprint(),
            "inputs": {key: value.manifest for key, value in self.inputs.items()},
            "development_registry": {key: registry(rows) for key, rows in self.splits.items()},
            "holdout_evaluated": False,
            "production_promoted": False,
            "local_behavior_gates": self.local_gate is not None,
            "config": self.config.model_dump(mode="json"),
        }
        (self.output / "selected-guidance.txt").write_text(guidance)
        write_json(self.output / "selection.json", selection)
        return selection


def seed_builds(study: TrainingFirstStudy, source: Path, *, briefs: set[str] | None = None) -> None:
    """Reuse completed fresh initial-guidance builds, retaining their original trace."""
    experiment = json.loads((source / "experiment.json").read_text())
    if experiment.get("source_fingerprint") != source_fingerprint():
        raise ValueError("Seed builder source differs from current frozen source")
    guidance = validate_guidance(study.config.guidance.read_text())
    requested = set(study.config.briefs) if briefs is None else briefs
    if not requested or requested - set(study.config.briefs):
        raise ValueError("Seed briefs must be a nonempty subset of configured briefs")
    checked = []
    for brief in study.config.briefs:
        if brief not in requested:
            continue
        parent = source / brief
        events = [json.loads(line) for line in (parent / "trace.jsonl").read_text().splitlines()]
        starts = [event for event in events if event.get("event") == "builder_start"]
        ends = [event for event in events if event.get("event") == "builder_end"]
        if len(starts) != 1 or len(ends) != 1 or ends[0].get("status") != "locally_validated":
            raise ValueError("Seed must be a completed fresh validated generation")
        if (
            starts[0].get("model") != study.config.model
            or starts[0].get("reasoning") != study.config.reasoning
        ):
            raise ValueError("Seed model configuration differs")
        if any(
            "reuse" in event.get("event", "") or "repair" in event.get("event", "")
            for event in events
        ):
            raise ValueError("Seed generation may not reuse policy or repair another build")
        if validate_guidance((parent / "guidance.txt").read_text()) != guidance:
            raise ValueError("Seed guidance differs")
        manifest = json.loads((parent / "inputs.json").read_text())

        def identity(rows):
            return sorted((row["source"], row["sha256"]) for row in rows)

        if identity(manifest) != identity(study.inputs[brief].manifest):
            raise ValueError("Seed inputs differ")
        files = [
            p for p in parent.iterdir() if p.is_file() and p.suffix in (".json", ".jsonl", ".txt")
        ]
        if any(p.is_symlink() for p in files) or not all(
            (parent / name).is_file()
            for name in ("workflow.json", "policy.json", "rule-nodes.json", "inputs.json")
        ):
            raise ValueError("Seed artifacts are incomplete or unsafe")
        records = list((parent / "autobench").glob("builder-*"))
        if len(records) != 1:
            raise ValueError("Seed needs one immutable native builder record")
        record = records[0]
        captured = replay_experiment(record)
        manifests = [
            artifact
            for run in captured.runs
            for artifact in run.task_result.artifacts
            if artifact.name == "evidence-manifest"
        ]
        if len(manifests) != 1:
            raise ValueError("Seed builder evidence manifest is missing or ambiguous")
        entries = yaml.safe_load((record / str(manifests[0].value)).read_text())["payload"]
        hashes = {entry["path"]: entry["source_sha256"] for entry in entries}
        for name in (
            "workflow.json",
            "policy.json",
            "rule-nodes.json",
            "inputs.json",
            "guidance.txt",
        ):
            if hashes.get(name) != hashlib.sha256((parent / name).read_bytes()).hexdigest():
                raise ValueError("Seed artifact changed after its native builder capture")
        checked.append((brief, parent, files))
    if len(checked) > study.config.max_builds:
        raise ValueError("Seed builds exceed the total build budget")
    provenance = []
    for brief, parent, files in checked:
        study.build_count += 1
        destination = study.output / "candidates" / f"{study.build_count:03d}-{brief}-seed"
        destination.mkdir(parents=True)
        for path in files:
            shutil.copyfile(path, destination / path.name)
        provenance.append(
            {
                "brief": brief,
                "source": str(parent),
                "destination": str(destination.relative_to(study.output)),
                "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
            }
        )
        study.builds[(brief, fingerprint(guidance))] = destination
    write_json(study.output / "seed-builds.json", provenance)


def seed_recovered_training(study: TrainingFirstStudy, recovery: Path) -> None:
    """Reuse one clean, immutable training repetition for the exact same initial build."""
    if study.config.train_repetitions != 1:
        raise ValueError("Recovered training import requires exactly one training repetition")
    cases = json.loads((recovery / "practice/cases.json").read_text())
    briefs = {row["brief"] for row in cases}
    if len(briefs) != 1:
        raise ValueError("Recovered training must belong to exactly one brief")
    brief = briefs.pop()
    batch = next((e.inputs for e in study.examples("train") if e.inputs.brief == brief), None)
    if batch is None or fingerprint(cases) != fingerprint(batch.cases):
        raise ValueError("Recovered cases differ from this study's exact training batch")
    guidance = validate_guidance(study.config.guidance.read_text())
    parent = json.loads((recovery / "parent.json").read_text())
    if parent.get("guidance_sha256") != fingerprint(guidance):
        raise ValueError("Recovered training guidance differs")
    health = json.loads((recovery / "practice/runtime-health.json").read_text())
    if health.get("usable_as_clean_model_comparison") is not True or any(
        health.get(field) != []
        for field in (
            "infrastructure_affected_cases",
            "unresolved_runtime_error_cases",
            "simulator_error_cases",
            "incomplete_evidence_cases",
        )
    ):
        raise ValueError("Recovered training evidence is not clean and complete")
    report = json.loads((recovery / "practice/results.json").read_text())
    result = repeated_score([report], [row["id"] for row in cases])
    records = list((recovery / "autobench").glob("existing_simulation_recovery-*"))
    if len(records) != 1:
        raise ValueError("Recovered training requires one immutable native recovery record")
    record = records[0]
    captured = replay_experiment(record)
    manifests = [
        artifact
        for run in captured.runs
        for artifact in run.task_result.artifacts
        if artifact.name == "evidence-manifest"
    ]
    if len(manifests) != 1:
        raise ValueError("Recovered training evidence manifest is missing or ambiguous")
    entries = yaml.safe_load((record / str(manifests[0].value)).read_text())["payload"]
    hashes = {entry["path"]: entry["source_sha256"] for entry in entries}
    files = [*recovery.glob("*.json"), *recovery.joinpath("practice").glob("*.json")]
    for path in files:
        relative = str(path.relative_to(recovery))
        if (
            path.is_symlink()
            or hashes.get(relative) != hashlib.sha256(path.read_bytes()).hexdigest()
        ):
            raise ValueError("Recovered training changed after its immutable capture")
    built = study.build(brief, guidance)
    if (built / "workflow.json").read_bytes() != (recovery / "workflow.json").read_bytes():
        raise ValueError("Recovered training workflow differs from the current initial build")
    destination = study.output / "reused-training" / brief
    repeat = destination / "repeat-1"
    (repeat / "practice").mkdir(parents=True, exist_ok=False)
    for path in files:
        shutil.copyfile(path, repeat / path.relative_to(recovery))
    result.update(
        path=str(destination.relative_to(study.output)),
        build=str(built.relative_to(study.output)),
        guidance_sha256=fingerprint(guidance),
        reused_training=True,
    )
    enrich_training_feedback(study.output, result)
    study.training_cache[(fingerprint(guidance), brief, fingerprint(cases))] = copy.deepcopy(result)
    write_json(
        destination / "provenance.json",
        {
            "source": str(recovery.resolve()),
            "native_record": str(record.resolve()),
            "simulation_id": report.get("simulation_id"),
            "iteration_id": report.get("iteration_id"),
            "case_sha256": fingerprint(cases),
            "source_hashes": hashes,
            "scope": "exact initial-guidance training batch only; no validation reuse",
        },
    )
    write_json(destination / "evaluation.json", result)
    emit("training_evidence_imported", brief=brief, score=result["score"], source=str(recovery))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed-build-root", type=Path)
    parser.add_argument("--seed-brief", choices=("A", "B"), action="append")
    parser.add_argument("--local-gates", action="store_true")
    parser.add_argument("--reuse-training", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.seed_brief and not args.seed_build_root:
        parser.error("--seed-brief requires --seed-build-root")
    if args.reuse_training and not args.seed_build_root:
        parser.error("--reuse-training requires --seed-build-root")
    config = StudyConfig.read(args.config)
    splits = load_development(config)
    validate_guidance(config.guidance.read_text())
    with workspace_lock():
        args.out.mkdir(parents=True, exist_ok=False)
        write_json(args.out / "config.json", config.model_dump(mode="json"))
        (args.out / "driver-source.txt").write_text(Path(__file__).read_text())
        (args.out / "data").mkdir()
        for split, rows in splits.items():
            write_json(args.out / "data" / f"{split}.json", rows)
        study = TrainingFirstStudy(
            config,
            args.out.resolve(),
            splits,
            local_gate=local_behavior_gate if args.local_gates else None,
            practice_factory=ReadResilientPractice,
        )

        def run():
            if args.seed_build_root:
                seed_builds(
                    study,
                    args.seed_build_root.resolve(),
                    briefs=set(args.seed_brief) if args.seed_brief else None,
                )
            for recovery in args.reuse_training:
                seed_recovered_training(study, recovery.resolve())
            return study.optimize()

        result = capture_sync(
            args.out.resolve(),
            "training_first_optimization",
            run,
            inputs={
                "search_split": "train",
                "final_selection_split": "validation",
                "holdout_access": False,
            },
        )
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
