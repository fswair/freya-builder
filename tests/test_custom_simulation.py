import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from freya_hackathon.builder import write_json

MODULE_PATH = Path(__file__).parents[1] / "diagnostics" / "run_custom_simulation.py"
SPEC = importlib.util.spec_from_file_location("custom_simulation", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
simulation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(simulation)


@pytest.mark.parametrize("changed", [False, True])
def test_changed_server_results_keep_both_call_snapshots_and_do_not_edit_export(
    tmp_path, monkeypatch, changed
):
    export = tmp_path / "export.json"
    export_bytes = b'{"agent_id":"agent","workflow_id":"workflow","workflow":{"nodes":{}}}'
    export.write_bytes(export_bytes)
    cases = tmp_path / "cases.json"
    write_json(
        cases,
        [
            {
                "id": "CASE-1",
                "title": "One request",
                "script": "Ask one question.",
                "variables": {"reference": "synthetic"},
                "checks": ["The question is answered."],
            }
        ],
    )
    calls = []

    class FakePractice:
        def __init__(self, run, seconds):
            self.output = run / "practice"
            self.output.mkdir()
            self.reads = 0
            self.client = SimpleNamespace(close=lambda: None)
            self.freya = SimpleNamespace(
                create_personas=lambda specs: {"personas": [{"id": "persona"}]},
                get_iteration=self.iteration,
                get_call=self.call,
            )

        def simulate(self, agent, name, variables, *, max_seconds, concurrency):
            assert agent == "agent"
            assert variables == {"persona": {"reference": "synthetic"}}
            return "simulation", "iteration", "completed"

        def iteration(self, sim_id, iteration_id):
            self.reads += 1
            return {
                "runs": [
                    {
                        "id": "run",
                        "personaId": "persona",
                        "personaName": "CASE-1 One request",
                        "callId": "second" if changed and self.reads == 2 else "first",
                        "outcome": "fail" if changed and self.reads == 2 else "pass",
                        "reason": "Recorded verdict",
                    }
                ]
            }

        def call(self, call_id):
            calls.append(call_id)
            write_json(self.output / f"call-{call_id}.json", {"call": {"id": call_id}})

        def trace(self, event, **fields):
            pass

    monkeypatch.setattr(simulation, "Practice", FakePractice)
    monkeypatch.setattr(simulation, "capture_sync", lambda output, kind, action, **kw: action())
    monkeypatch.setattr(simulation, "audit_runtime", lambda output: {})
    output = tmp_path / "evaluation"
    report = simulation.run(export, cases, output, 120)
    assert report["official_score"] is False
    assert report["snapshot_stable"] is not changed
    assert report["passed"] == (0 if changed else 1)
    assert calls == (["first", "second"] if changed else ["first"])
    assert export.read_bytes() == export_bytes
    first = json.loads((output / "practice/iteration-001.json").read_text())
    second = json.loads((output / "practice/iteration-002.json").read_text())
    assert (first != second) is changed
    assert json.loads((output / "practice/iteration.json").read_text()) == second
    assert report["rows"][0]["simulation_match_count"] == 1


@pytest.mark.parametrize("cases", [[None], [{"id": "x"}], []])
def test_invalid_personas_are_rejected_before_any_network_operation(tmp_path, cases):
    path = tmp_path / "cases.json"
    write_json(path, cases)
    with pytest.raises(ValueError):
        simulation.load_cases(path)
