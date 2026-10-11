"""A negated date before a dash must not erase its explicit replacement."""

import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def code():
    workflow = json.loads(
        (ROOT / "benchmarks/development-references-v9/single-product.json").read_text()
    )
    return workflow["nodes"]["advance"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]


@pytest.mark.parametrize("dash", ["—", "–", "-"])
def test_corrected_date_survives_repeated_interrupted_amount(code, dash):
    values = json.loads((ROOT / "tests/fixtures/a-dash-date-correction.json").read_text())
    values["turn_raw"] = values["turn_raw"].replace("—", dash)
    result = execute_python(code, {"vars": values})["vars"]
    product = json.loads(result["call_state"])["products"]["p1"]
    assert product["held_date_words"].lower() == "this friday"
    assert result["next"] != "create"
    for _ in range(2):
        result.update(
            turn_raw="I can pay the full 125",
            turn_amount="125",
            turn_amount_reference="full",
            turn_intent="proposal",
            turn_coverage="incomplete",
        )
        result = execute_python(code, {"vars": result})["vars"]
        assert result["next"] != "create"
    result.update(
        turn_raw="I can pay the full 1250.",
        turn_amount="1250",
        turn_amount_reference="full",
        turn_intent="proposal",
        turn_coverage="complete",
    )
    result = execute_python(code, {"vars": result})["vars"]
    product = json.loads(result["call_state"])["products"]["p1"]
    assert product["date"] == "2026-10-16"
    assert product["amount"] == 1250
    assert not product["confirmed"]
    assert result["next"] == "confirm_speak"


def test_rejected_date_without_replacement_is_not_adopted(code):
    values = json.loads((ROOT / "tests/fixtures/a-dash-date-correction.json").read_text())
    values.update(turn_raw="Not next Friday.", turn_date="next Friday", turn_coverage="incomplete")
    result = execute_python(code, {"vars": values})["vars"]
    product = json.loads(result["call_state"])["products"]["p1"]
    assert product.get("held_date_words", "") == ""
    assert not product["confirmed"]
    assert result["next"] != "create"
