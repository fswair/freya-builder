"""Conversational closing dates are not requests to change recorded promises."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10"])
def code(request: pytest.FixtureRequest) -> str:
    workflow = json.loads(
        (ROOT / f"benchmarks/development-references-{request.param}/multi-product.json").read_text()
    )
    return workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]


@pytest.fixture
def variables() -> dict:
    return json.loads((ROOT / "tests/fixtures/b-post-record-state.json").read_text())


@pytest.mark.parametrize(
    "raw",
    [
        "No, that is all for today.",
        "That's all for today, thank you.",
        "Nothing else today, thanks.",
    ],
)
def test_polite_completion_does_not_invent_a_change_request(
    code: str, variables: dict, raw: str
) -> None:
    before = json.loads(variables["state"])
    result = execute_python(code, {"vars": {**variables, "turn_raw": raw}})["vars"]
    after = json.loads(result["state"])
    assert result["route"] == "farewell" and result["outcome"] == "promise"
    assert result["farewell"] == "Thank you. Goodbye."
    assert after["products"] == before["products"]


def test_actual_change_request_keeps_recorded_promises_immutable(
    code: str, variables: dict
) -> None:
    before = json.loads(variables["state"])
    result = execute_python(
        code,
        {"vars": {**variables, "turn_raw": "I want to move the card payment to tomorrow."}},
    )["vars"]
    after = json.loads(result["state"])
    assert result["route"] == "farewell" and result["outcome"] == "promise"
    assert result["farewell"].count("mobile app") == 1
    assert after["products"] == before["products"]
