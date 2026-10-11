"""Deploy a generated workflow and run the official synthetic text practice cases."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from freya_hackathon.builder import KIT, ROOT, utc_now, write_json
from freya_hackathon.export_parity import require_preserved
from freya_hackathon.recording import capture_sync, emit, record_cases
from freya_hackathon.runtime_audit import audit_runtime
from freya_hackathon.workflow import walk


def tool_locations(workflow: dict) -> set[str]:
    return {
        path for path, value in walk(workflow) if isinstance(value, dict) and "handler" in value
    }


class WorkflowRejected(ValueError):
    """Freya rejected a workflow before saving it or starting a simulation."""


def iteration_runs_by_case(iteration: dict) -> dict[str, list[dict]]:
    """Keep duplicate/missing joins visible instead of selecting an arbitrary run."""
    grouped: dict[str, list[dict]] = {}
    runs = iteration.get("runs")
    for row in runs if isinstance(runs, list) else []:
        if not isinstance(row, dict):
            continue
        name = row.get("personaName")
        if isinstance(name, str) and name.split():
            grouped.setdefault(name.split()[0], []).append(row)
    return grouped


def preserve_simulation_metadata(rows: list[dict], iteration: dict) -> None:
    """Retain the simulator verdict separately from combined conversation/ledger scoring."""
    grouped = iteration_runs_by_case(iteration)
    for row in rows:
        matches = grouped.get(row["id"], [])
        judge = matches[0] if len(matches) == 1 else {}
        row.update(
            simulation_run_id=judge.get("id"),
            simulation_outcome=judge.get("outcome"),
            simulation_reason=judge.get("reason"),
            simulation_match_count=len(matches),
        )
        error = None
        if len(matches) != 1:
            error = "missing iteration run" if not matches else "ambiguous iteration runs"
        elif not isinstance(judge.get("id"), str) or not judge["id"]:
            error = "missing simulation run ID"
        elif row.get("call_id") != judge.get("callId"):
            error = "call ID does not match iteration run"
        if error:
            row["simulation_join_error"] = error
            failure = "simulation evidence: " + error
        elif judge.get("outcome") != "pass":
            failure = "conversation: " + str(judge.get("reason") or "no verdict")[:400]
        else:
            continue
        row["pass"] = False
        if failure not in row["failed"]:
            row["failed"].append(failure)


class Practice:
    def __init__(self, run: Path, seconds: float = 900, *, resume: bool = False):
        self.run = run.resolve()
        self.output = self.run / "practice"
        self.output.mkdir(exist_ok=resume)
        self.deadline = time.monotonic() + seconds
        self.client = httpx.Client(timeout=45)
        load_dotenv(ROOT / ".env")
        if not os.environ.get("FREYA_API_KEY"):
            raise ValueError("FREYA_API_KEY is required")
        key_file = ROOT / ".freya-team-key"
        if not os.environ.get("FREYA_BANK_TEAM_KEY"):
            if not key_file.exists():
                with open(key_file, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as f:
                    f.write(secrets.token_urlsafe(32))
            os.environ["FREYA_BANK_TEAM_KEY"] = key_file.read_text().strip()
        if len(os.environ["FREYA_BANK_TEAM_KEY"]) < 16:
            raise ValueError("FREYA_BANK_TEAM_KEY must have at least 16 characters")
        sys.path[:0] = [str(KIT), str(KIT / "tools")]
        self.freya: Any = importlib.import_module("freya")
        self.cases: Any = importlib.import_module("run_cases")
        self.freya.api = self.api
        self.freya.run_simulation = self.simulate
        self.cases.http = self.bank
        self.freya.AGENT_LLM = {"provider": "experimental-5", "model": "experimental-5"}

    def trace(self, event: str, **fields: object) -> None:
        with (self.run / "trace.jsonl").open("a") as f:
            f.write(json.dumps({"time": utc_now(), "event": event, **fields}) + "\n")
        emit(event, **fields)

    def remaining(self) -> float:
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError("Practice deadline exceeded; inspect the recorded simulation ID")
        return min(45, value)

    def api(self, method: str, path: str, body: Any = None, **params: Any) -> Any:
        response = self.client.request(
            method,
            self.freya.URL + path,
            json=body,
            params=params,
            headers=self.freya.HEADERS,
            timeout=self.remaining(),
        )
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and result.get("success") is False:
            # Keep server feedback locally for diagnosis; never include auth headers.
            write_json(self.output / "api-rejection.json", result)
            raise RuntimeError("Freya rejected the request; see practice/api-rejection.json")
        result = (
            result.get("data", {}).get("result", result) if isinstance(result, dict) else result
        )
        if method == "GET" and path.startswith("/api/v2/call/"):
            write_json(self.output / f"call-{path.rsplit('/', 1)[1]}.json", result)
        return result

    def bank(self, path: str, body: Any = None) -> Any:
        if not path.startswith(("/v1/practice/sessions", "/v1/practice/ledger/")):
            raise ValueError("Practice runner only accesses synthetic sessions and ledgers")
        if path == "/v1/practice/sessions" and isinstance(body, dict) and "case" in body:
            source = getattr(self, "session_cases", {}).get(body["case"], body["case"])
            if source != body["case"]:
                self.trace("practice_seed_alias", case_id=body["case"], seed_case_id=source)
                body = {**body, "case": source}
        response = self.client.request(
            "POST" if body is not None else "GET",
            "https://freya-bank-mock.freyavoice.workers.dev" + path,
            json=body,
            headers={"x-team-key": os.environ["FREYA_BANK_TEAM_KEY"]},
            timeout=self.remaining(),
        )
        response.raise_for_status()
        value = response.json()
        if path.startswith("/v1/practice/ledger/"):
            write_json(self.output / f"ledger-{path.rsplit('/', 1)[1]}.json", value)
        return value

    def simulate(
        self,
        agent_id: str,
        name: str,
        variables_by_persona: dict,
        max_seconds: int = 420,
        concurrency: int = 5,
    ) -> tuple[str, str, str]:
        model = self.freya.AGENT_LLM
        write_json(self.output / "simulation-inputs.json", variables_by_persona)
        result = self.api(
            "POST",
            "/api/v2/simulations",
            {
                "agentId": agent_id,
                "name": name,
                "mode": "chat",
                "selectedPersonaIds": list(variables_by_persona),
                "customerVariablesMap": variables_by_persona,
                "maxConcurrentCalls": concurrency,
                "runsPerPersona": 1,
                "maxCallDurationSeconds": max_seconds,
                "assistantLlmOverride": model,
                "routingLlmOverride": model,
                "extractionLlmOverride": model,
                "personaLlmOverride": self.freya.SIM_LLM,
                "analysisLlmOverride": self.freya.SIM_LLM,
            },
        )
        sim_id = (result.get("simulation") or result)["id"]
        self.trace(
            "simulation_start", simulation_id=sim_id, mode="chat", cases=len(variables_by_persona)
        )
        write_json(self.output / "simulation.json", {"simulation_id": sim_id})
        while True:
            time.sleep(min(10, self.remaining()))
            state = self.api("GET", f"/api/v2/simulations/{sim_id}")
            state = state.get("simulation") or state
            write_json(self.output / "simulation-state.json", state)
            if state.get("status") in ("completed", "failed", "paused"):
                break
        iterations = self.api("GET", f"/api/v2/simulations/{sim_id}/iterations")
        iterations = iterations.get("iterations") or iterations.get("items") or iterations
        self.trace("simulation_end", simulation_id=sim_id, status=state["status"])
        return sim_id, iterations[0]["id"], state["status"]

    def collect(self) -> dict:
        """Recover a timed-out local wait without launching another simulation."""
        report = capture_sync(self.run, "collect", self._collect)
        record_cases(self.run)
        return report

    def _collect(self) -> dict:
        saved = json.loads((self.output / "deployment.json").read_text())
        sim_id = json.loads((self.output / "simulation.json").read_text())["simulation_id"]
        cases = {c["id"]: c for c in json.loads((self.output / "cases.json").read_text())}
        self.trace("practice_collection_resumed", simulation_id=sim_id)
        while True:
            response = self.api("GET", f"/api/v2/simulations/{sim_id}")
            state = response.get("simulation") or response
            write_json(self.output / "simulation-state.json", state)
            if state["status"] in ("completed", "failed", "paused"):
                break
            time.sleep(min(10, self.remaining()))
        its = self.api("GET", f"/api/v2/simulations/{sim_id}/iterations")
        its = its.get("iterations") or its.get("items") or its
        iteration = self.freya.get_iteration(sim_id, its[0]["id"])
        write_json(self.output / "iteration.json", iteration)
        by_case = iteration_runs_by_case(iteration)
        rows = []
        for case_id, case in cases.items():
            matches = by_case.get(case_id, [])
            judge = matches[0] if len(matches) == 1 else {}
            call_id = judge.get("callId")
            call = self.freya.get_call(call_id) if call_id else {}
            customer_id = None
            for message in call.get("messages", []):
                if message.get("role") != "system":
                    continue
                try:
                    event = json.loads(message.get("message", ""))
                except (ValueError, TypeError):
                    continue
                value = event.get("variable_table", {}).get("customer_id")
                if value:
                    customer_id = value.get("value") if isinstance(value, dict) else value
                    break
            if customer_id:
                ledger = self.bank("/v1/practice/ledger/" + customer_id)
                score = self.cases.score_ledger(case, ledger)
                tools = [c["tool"] for c in ledger["calls"]]
            else:
                score = {"failed": ["Missing practice customer ID; ledger could not be scored"]}
                tools = []
            if judge.get("outcome") != "pass":
                score["failed"].append(
                    "conversation: " + str(judge.get("reason") or "no verdict")[:400]
                )
            rows.append(
                {
                    "id": case_id,
                    "pass": not score["failed"],
                    "failed": score["failed"],
                    "call_id": call_id,
                    "customer_id": customer_id,
                    "tools": tools,
                    "ended": call.get("endedReason"),
                }
            )
        preserve_simulation_metadata(rows, iteration)
        report = {
            **saved,
            "simulation_id": sim_id,
            "iteration_id": its[0]["id"],
            "status": state["status"],
            "passed": sum(bool(r["pass"]) for r in rows),
            "total": len(rows),
            "runs": 1,
            "recovered_after_local_timeout": True,
            "rows": rows,
        }
        write_json(self.output / "results.json", report)
        audit_runtime(self.run)
        self.trace("practice_results_recovered", passed=report["passed"], total=len(rows), runs=1)
        return report

    def execute(self, cases_path: Path, brief: str, slot: str, only: str | None = None) -> dict:
        report = capture_sync(
            self.run,
            "practice",
            lambda: self._execute(cases_path, brief, slot, only),
            factors={"freya.runtime_model": "experimental-5", "brief": brief},
            inputs={
                "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
                "only": only,
            },
        )
        record_cases(self.run)
        return report

    def _execute(self, cases_path: Path, brief: str, slot: str, only: str | None = None) -> dict:
        workflow = json.loads((self.run / "workflow.json").read_text())
        self.trace("practice_start", brief=brief, slot=slot)
        validation = self.freya.validate_workflow(slot, workflow)
        write_json(self.output / "freya-validation.json", validation)
        if validation.get("valid") is not True:
            raise WorkflowRejected("Workflow failed Freya dry-run validation")
        saved = self.freya.save_workflow(slot, workflow, str(ROOT / ".freya-agents.json"))
        # Creation and update have different publishing semantics. Always check actual versions.
        if "version" not in saved:
            saved["version"] = self.freya.update_workflow(saved["workflow_id"], workflow)
        write_json(self.output / "deployment.json", saved)
        self.freya.export_workflow(saved["workflow_id"], str(self.output / "export.json"))
        exported = json.loads((self.output / "export.json").read_text())["workflow"]
        if tool_locations(workflow) - tool_locations(exported):
            raise ValueError("Freya dropped tool handlers while saving the workflow")
        require_preserved(workflow, exported)
        versions = self.api(
            "GET",
            f"/api/v2/versions/workflow/{saved['workflow_id']}",
            workspaceId=self.freya.workspace_id(),
        )["versions"]
        if not any(
            v["versionNumber"] == saved["version"] and v.get("isPublished") for v in versions
        ):
            raise RuntimeError("Published workflow version was not confirmed")
        self.trace(
            "workflow_published",
            **saved,
            node_count=len(workflow.get("nodes", {})) + len(workflow.get("global_nodes", {})),
            tool_count=len(tool_locations(workflow)),
        )
        cases = [
            c
            for c in json.loads(cases_path.read_text())
            if c["brief"] == brief and (not only or c["id"] in only.split(","))
        ]
        if not cases:
            raise ValueError("No practice cases selected")
        if len({case["id"] for case in cases}) != len(cases):
            raise ValueError("Practice case IDs must be unique")
        self.session_cases = {
            case["id"]: case.get("practice_session_case", case["id"]) for case in cases
        }
        write_json(self.output / "cases.json", cases)
        workspace = self.freya.workspace_id()
        store = ROOT / ".personas.json"
        cache = json.loads(store.read_text()) if store.exists() else {}
        ids = {}
        for case in cases:
            spec = self.cases.persona_for(case, {"identity": case["identity"]})
            key = (
                workspace
                + ":"
                + hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
            )
            if key not in cache:
                cache[key] = self.freya.create_personas([spec])["personas"][0]["id"]
                write_json(store, cache)
            ids[case["id"]] = cache[key]
        rows, sim, iteration, status, seconds, median = self.cases.run_once(
            saved["agent_id"], cases, ids
        )
        iteration_data = self.freya.get_iteration(sim, iteration)
        write_json(self.output / "iteration.json", iteration_data)
        preserve_simulation_metadata(rows, iteration_data)
        report = {
            **saved,
            "simulation_id": sim,
            "iteration_id": iteration,
            "status": status,
            "passed": sum(row["pass"] for row in rows),
            "total": len(rows),
            "runs": 1,
            "seconds": seconds,
            "median_slowest_turn_seconds": median,
            "rows": rows,
        }
        write_json(self.output / "results.json", report)
        audit_runtime(self.run)
        self.trace("practice_results", passed=report["passed"], total=len(rows), runs=1)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--cases", type=Path)
    parser.add_argument(
        "--collect", action="store_true", help="Recover existing simulation results"
    )
    parser.add_argument("--brief", choices=("A", "B"), default="A")
    parser.add_argument("--slot", default="pydantic-builder-A")
    parser.add_argument("--only")
    parser.add_argument("--seconds", type=float, default=900)
    args = parser.parse_args()
    if not args.collect and args.cases is None:
        parser.error("--cases is required unless --collect is used")
    practice = Practice(args.run, args.seconds, resume=args.collect)
    try:
        result = (
            practice.collect()
            if args.collect
            else practice.execute(args.cases, args.brief, args.slot, args.only)
        )
        print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))
    except Exception as exc:
        practice.trace("practice_error", error_type=type(exc).__name__)
        raise
    finally:
        practice.client.close()


if __name__ == "__main__":
    main()
