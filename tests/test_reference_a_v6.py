"""Observed complete restatements and explicit unaffordability must make progress."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["v6", "v8", "v9"])
def code(request: pytest.FixtureRequest) -> str:
    path = ROOT / f"benchmarks/development-references-{request.param}/single-product.json"
    workflow = json.loads(path.read_text())
    return workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]


def test_explicit_unaffordability_and_plan_request_reaches_restructuring(code: str) -> None:
    values = json.loads(
        (ROOT / "tests/fixtures/a-explicit-unaffordability-plan-turn.json").read_text()
    )
    result = execute_python(code, {"vars": values})["vars"]
    assert result["next"] == "transfer"
    assert result["transfer_queue"] == "restructuring"


def test_inflected_confirmation_completes_amount_but_needs_fresh_readback(code: str) -> None:
    values = json.loads((ROOT / "tests/fixtures/a-inflected-confirmation-turn.json").read_text())
    result = execute_python(code, {"vars": values})["vars"]
    state = json.loads(result["call_state"])
    product = state["products"]["p1"]
    assert result["next"] == "confirm_speak"
    assert state["stage"] == "confirm"
    assert product["amount"] == 1250
    assert product["date"] == "2026-10-16"
    assert not product["terms_unresolved"]
    assert not product["confirmed"]
