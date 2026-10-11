"""A failed controller must not repeat a bank write or invent its outcome."""

import ast
import json
from pathlib import Path

import pytest

from freya_hackathon.offline import execute_python

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(
    params=[
        (version, product)
        for version in ("v8", "v9")
        for product in ("single-product", "multi-product")
    ]
)
def workflow(request):
    version, product = request.param
    return json.loads(
        (ROOT / f"benchmarks/development-references-{version}/{product}.json").read_text()
    )


def matches(condition, variables):
    if "conditions" in condition:
        values = [matches(item, variables) for item in condition["conditions"]]
        return all(values) if condition["logic"] == "and" else any(values)
    operator = condition["operator"]
    if operator == "always":
        return True
    value = variables.get(condition["variable"])
    if operator == "equals":
        return value == condition["operand"]
    if operator == "greater_than":
        return float(value or 0) > condition["operand"]
    raise AssertionError(operator)


def route(node, variables):
    return next(
        transition["target_node"]
        for transition in node["transitions"]
        if matches(transition["condition"], variables)
    )


def recover(workflow, *, ok=False, recorded=False, transferred=False):
    multi = "decide" in workflow["nodes"]
    state = {"verified": True, "queue": "support", "requested_transfer_reason": "support"}
    state["products"] = [{"status": "recorded" if recorded else "agreed"}]
    state["pending"] = "transfer" if transferred else "create"
    values = {
        "state" if multi else "call_state": json.dumps(state),
        "event": "transfer_to_agent" if transferred else "create_promise",
        "api_data": json.dumps({"ok": ok}),
        "next": "create",
        "route": "create_promise",
    }
    code = workflow["nodes"]["runtime_recovery"]["pre_actions"][0]["function"]["parameters"][
        "properties"
    ]["code"]["default"]
    return execute_python(code, {"vars": values})["vars"]


@pytest.mark.parametrize("error", [{"status": "error"}, {"failure": 1}])
def test_controller_failure_overrides_stale_financial_write(workflow, error):
    controller = "decide" if "decide" in workflow["nodes"] else "advance"
    values = {"next": "create", "route": "create_promise"}
    values[f"{controller}_response.status"] = error.get("status")
    values[f"{controller}_failure_count"] = error.get("failure", 0)
    assert route(workflow["nodes"][controller], values) == "runtime_recovery"


def test_successful_bank_write_is_preserved_without_another_create(workflow):
    values = recover(workflow, ok=True)
    assert (values["outcome"], values["reason"]) == ("promise", "")
    assert values["route"] == "farewell"
    assert values["next"] == "goodbye"
    assert "text message" in values.get("farewell", values.get("closing_message", ""))


def test_http_success_without_business_success_is_not_a_promise(workflow):
    values = recover(workflow)
    assert (values["outcome"], values["reason"]) == ("no_promise", "service_error")


def test_previously_recorded_promise_survives_a_later_runtime_failure(workflow):
    values = recover(workflow, recorded=True)
    assert values["outcome"] == "promise"


def test_successful_transfer_takes_precedence_over_existing_promise(workflow):
    values = recover(workflow, ok=True, recorded=True, transferred=True)
    assert (values["outcome"], values["reason"]) == ("transferred", "support")


def test_second_sandbox_failure_never_repeats_a_financial_action(workflow):
    nodes = workflow["nodes"]
    next_node = route(nodes["runtime_recovery"], {"runtime_recovery_response.status": "error"})
    assert next_node == "runtime_unavailable"
    assert not nodes[next_node].get("pre_actions")
    terminal = route(nodes[next_node], {})
    if "terminal" not in nodes[terminal]:
        # A best-effort, single final ledger close is also valid. It must never
        # route back into failed code, payment creation, transfer, or another retry.
        close = nodes[terminal]
        assert close["node_type"] == "api_request"
        assert len(close["pre_actions"]) == 1
        action = close["pre_actions"][0]
        function = action["function"]
        assert function["handler"] == "__function__:api_call"
        properties = function["parameters"]["properties"]
        assert properties["method"]["default"] == "POST"
        assert properties["url"]["default"].endswith("/close_call")
        assert len(close["transitions"]) == 1
        assert close["transitions"][0]["condition"] == {"operator": "always"}
        for pending, ok, recorded, expected in [
            ("create", True, False, ("promise", None)),
            ("create", False, False, ("no_promise", "service_error")),
            ("transfer", False, True, ("promise", None)),
            ("transfer", True, True, ("transferred", "human")),
        ]:
            multi = "decide" in nodes
            product = {
                "product_id": "p1",
                "recorded": recorded,
                "status": "recorded" if recorded else "agreed",
            }
            state = {
                "verified": True,
                "requested_transfer_reason": "human",
                "queue": "general",
                "products": [product] if multi else {"p1": product},
            }
            if not multi:
                state["pending"] = pending
            values = {
                "state" if multi else "call_state": json.dumps(state),
                "api_data": json.dumps({"ok": ok}),
                "customer_id": "synthetic-probe",
            }
            if multi:
                # B carries the bank operation in vars.event and flattened API results,
                # not A's call_state.pending. Replay the actual node's preparation.
                operation = {"create": "create_promise", "transfer": "transfer_to_agent"}[pending]
                values = execute_python(
                    nodes[operation]["pre_actions"][0]["pre_process_code"],
                    {"vars": values, "config": {}},
                )["vars"]
                values[operation + "_response.data.ok"] = ok
                values["api_data"] = json.dumps({"ok": ok})
            result = execute_python(
                action["pre_process_code"], {"vars": values, "config": {"body": {}}}
            )
            body = result["config"]["body"]
            actual = body["outcome"], body.get("reason")
            assert actual == expected, {
                "expected": expected,
                "actual": actual,
                "pending_bank_operation": pending,
                "bank_business_ok": ok,
                "previously_recorded_promise": recorded,
            }
        terminal = route(close, {})
    assert nodes[terminal]["terminal"]["function"]["handler"] == "__function__:end_call"


@pytest.mark.parametrize("name", ["single-product", "multi-product"])
def test_compaction_preserves_all_existing_code_ast(name):
    old = json.loads((ROOT / f"benchmarks/development-references-v7/{name}.json").read_text())
    new = json.loads((ROOT / f"benchmarks/development-references-v8/{name}.json").read_text())
    for key, node in old["nodes"].items():
        if node.get("node_type") != "code":
            continue

        def code(value):
            return value["pre_actions"][0]["function"]["parameters"]["properties"]["code"][
                "default"
            ]

        assert ast.dump(ast.parse(code(node))) == ast.dump(ast.parse(code(new["nodes"][key])))
