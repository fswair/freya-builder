from __future__ import annotations

import json

import pytest

from freya_hackathon.offline import execute_python, matches, run_suite


def test_replay_supports_character_literals_used_by_generated_quote_guards() -> None:
    result = execute_python("vars['quote'] = chr(34)", {"vars": {}})
    assert result["vars"]["quote"] == '"'


def test_replay_preserves_freya_merge_and_uses_real_python() -> None:
    values = {"vars": {"old": "stale", "amount": "100"}}
    result = execute_python(
        "import json\nvars.pop('old')\nvars['state'] = json.dumps({'amount': int(vars['amount'])})",
        values,
    )
    assert result["vars"]["old"] == "stale"
    assert json.loads(result["vars"]["state"]) == {"amount": 100}
    assert values == {"vars": {"old": "stale", "amount": "100"}}


@pytest.mark.parametrize(
    "code",
    ["import os", "import urllib.request", "open('/tmp/offline-unwanted', 'w')", "vars.__class__"],
)
def test_worker_rejects_io_and_private_attributes(code: str) -> None:
    with pytest.raises(ValueError):
        execute_python(code, {"vars": {}})


@pytest.mark.parametrize(
    "dates, expected", [([], ""), (["2026-10-16", "2026-10-16"], "2026-10-16")]
)
def test_worker_resolves_single_distinct_date_with_iterator(dates, expected) -> None:
    result = execute_python(
        "vars['date'] = next(iter(set(vars['dates'])), '')", {"vars": {"dates": dates}}
    )
    assert result["vars"]["date"] == expected


@pytest.mark.parametrize("year, month, expected", [(2026, 13, [2027, 1]), (2026, 0, [2025, 12])])
def test_worker_supports_calendar_month_rollover(year, month, expected) -> None:
    result = execute_python(
        "year, month = divmod(vars['year'] * 12 + vars['month'] - 1, 12)\n"
        "vars['normalized'] = [year, month + 1]",
        {"vars": {"year": year, "month": month}},
    )
    assert result["vars"]["normalized"] == expected


def bank_action(name: str) -> dict:
    return {
        "function": {
            "handler": "__function__:api_call",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"default": f"https://never-contact.invalid/{name}"},
                    "method": {"default": "POST"},
                    "body": {
                        "type": "object",
                        "properties": {"amount": {"type": "number", "default": "{{amount}}"}},
                    },
                },
            },
        },
        "post_process_code": "vars['recorded'] = result['data']['ok']",
    }


def test_fake_bank_asserts_writes_and_detects_duplicate_close() -> None:
    workflow = {
        "initial_node": "write",
        "nodes": {
            "write": {
                "node_type": "api_request",
                "pre_actions": [bank_action("create_promise")],
                "passthrough_default_transition": "close",
            },
            "close": {
                "node_type": "api_request",
                "pre_actions": [bank_action("close_call")],
                "passthrough_default_transition": "end",
            },
            "end": {"node_type": "end_call"},
        },
    }
    suite: dict = {
        "cases": [
            {
                "id": "promise",
                "variables": {"amount": "125.5"},
                "responses": [
                    {
                        "operation": name,
                        "expect_body": {"amount": 125.5},
                        "result": {"data": {"ok": True}},
                    }
                    for name in ["create_promise", "close_call"]
                ],
                "expect": {
                    "node": "end",
                    "variables": {"recorded": True},
                    "operations": ["create_promise", "close_call"],
                },
            }
        ],
    }
    assert run_suite(workflow, suite)["passed"] == 1
    action = workflow["nodes"]["write"]["pre_actions"][0]
    action["pre_process_code"] = "config['body']['amount'] = 250.0"
    suite["cases"][0]["responses"][0]["expect_body"] = {"amount": 250.0}
    assert run_suite(workflow, suite)["passed"] == 1
    workflow["nodes"]["close"]["passthrough_default_transition"] = "close"
    failed = run_suite(workflow, suite)
    assert failed["passed"] == 0
    assert failed["rows"][0]["error"] == "Bank operation after close_call"


def test_unsupported_routing_cannot_silently_pass() -> None:
    with pytest.raises(ValueError, match="Unsupported offline routing"):
        matches({"operator": "llm_decides"}, {})
