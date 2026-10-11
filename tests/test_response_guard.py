"""A retained prior success cannot acknowledge a failed subsequent bank write."""

import copy
import json

import pytest

from freya_hackathon.compiler import compile_workflow
from freya_hackathon.offline import execute_python
from freya_hackathon.workflow import WorkflowBuild


@pytest.fixture
def workflow():
    api = {
        "node_type": "api_request",
        "pre_actions": [{"function": {"handler": "__function__:api_call"}}],
        "response_mapping": [{"path": "data", "variable": "api_data"}],
    }
    controller = """import json
vars['recorded'] = json.loads(vars.get('api_data', '{}')).get('ok') is True
"""
    draft = WorkflowBuild(
        workflow={
            "nodes": {
                "create_promise": copy.deepcopy(api),
                "add_note": copy.deepcopy(api),
                "decide": {
                    "pre_actions": [
                        {
                            "function": {
                                "handler": "__function__:code_exec",
                                "parameters": {"properties": {"code": {"default": controller}}},
                            }
                        }
                    ]
                },
            }
        },
        rule_nodes={},
    )
    compiled, changes = compile_workflow(draft)
    assert changes
    assert compile_workflow(compiled) == (compiled, [])
    return compiled.workflow


def prepare(workflow, values, source="create_promise"):
    code = workflow["nodes"][source]["pre_actions"][0]["pre_process_code"]
    return execute_python(code, {"vars": values})["vars"]


def decide(workflow, values):
    code = workflow["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"]["properties"][
        "code"
    ]["default"]
    return execute_python(code, {"vars": values})["vars"]


@pytest.mark.parametrize("status", ["error", "failed", "pending", "", None])
def test_stale_success_is_rejected_after_unsuccessful_request(workflow, status):
    values = prepare(workflow, {"create_promise_response.status": "success"})
    # Freya retains the first promise's data and maps it after the second fails.
    values.update(
        {"create_promise_response.status": status, "api_data": '{"ok":true,"promise_id":"pr1"}'}
    )
    result = decide(workflow, values)
    assert result["recorded"] is False
    assert json.loads(result["api_data"]) == {}


def test_prior_success_status_is_reset_before_the_next_request(workflow):
    values = prepare(workflow, {"create_promise_response.status": "success"})
    assert values["create_promise_response.status"] == "pending"
    values["api_data"] = '{"ok":true}'
    assert decide(workflow, values)["recorded"] is False


@pytest.mark.parametrize("ok", [True, False])
def test_current_success_preserves_the_business_result(workflow, ok):
    values = prepare(workflow, {})
    values.update({"create_promise_response.status": "success", "api_data": json.dumps({"ok": ok})})
    result = decide(workflow, values)
    assert result["recorded"] is ok
    assert json.loads(result["api_data"]) == {"ok": ok}


def test_previous_failed_api_does_not_poison_a_new_success(workflow):
    values = prepare(workflow, {})
    values["create_promise_response.status"] = "error"
    values = prepare(workflow, values, "add_note")
    values.update({"add_note_response.status": "success", "api_data": '{"ok":true}'})
    assert decide(workflow, values)["recorded"] is True


def test_controller_before_any_api_keeps_its_original_behavior(workflow):
    assert decide(workflow, {"api_data": '{"ok":true}'})["recorded"] is True
