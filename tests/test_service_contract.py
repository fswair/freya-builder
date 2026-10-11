from __future__ import annotations

import asyncio
import copy
import json
from contextlib import asynccontextmanager

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from freya_hackathon.builder import Builder, Inputs, load_inputs
from freya_hackathon.policy import Evidence, Policy, Rule
from freya_hackathon.service_contract import ServiceContract, bind_mock_url, validate_mock_url
from freya_hackathon.workflow import WorkflowBuild, api_operations

MOCK_URL = "https://example-mock.workers.dev"
SOURCE = 'POST /lookup requires a JSON body with the string field "reference".'
API = {
    "openapi": "3.0.3",
    "info": {"title": "Fixture", "version": "1"},
    "servers": [{"url": "https://old.example.invalid"}],
    "paths": {"/lookup": {"post": {
        "operationId": "lookup",
        "requestBody": {"content": {"application/json": {"schema": {
            "type": "object", "required": ["reference"],
            "properties": {"reference": {"type": "string"}},
        }}}},
        "responses": {"200": {"description": "Lookup response"}},
    }}},
}


def contract():
    return ServiceContract(
        openapi=copy.deepcopy(API),
        operations=[{"method": "POST", "path": "/lookup",
                     "evidence": [{"source": "brief-1", "quote": SOURCE}]}],
    )


def test_binding_overrides_every_server_scope_without_changing_request_fields():
    original = copy.deepcopy(API)
    original["paths"]["/lookup"]["servers"] = [{"url": "https://path.invalid"}]
    op = original["paths"]["/lookup"]["post"]
    op["servers"] = [{"url": "https://operation.invalid"}]
    schema = op["requestBody"]["content"]["application/json"]["schema"]
    schema["properties"]["servers"] = {"type": "array", "items": {"type": "string"}}
    before = copy.deepcopy(original)
    result = bind_mock_url(original, MOCK_URL)
    assert list(api_operations(result)) == [("POST", MOCK_URL + "/lookup")]
    assert result["paths"]["/lookup"]["post"]["requestBody"] == op["requestBody"]
    assert original == before


@pytest.mark.parametrize("url", ["http://localhost", "https://u:p@host", "https://host?key=x",
                                 "https://host#fragment", "https://{host}", "/relative"])
def test_mock_url_rejects_ambiguous_or_secret_bearing_configuration(url):
    with pytest.raises(ValueError, match="Mock URL"):
        validate_mock_url(url)


def test_contract_requires_source_evidence_for_all_operations():
    item = contract()
    assert ("POST", MOCK_URL + "/lookup") in api_operations(
        item.checked_api({"brief-1": SOURCE}, MOCK_URL)
    )
    with pytest.raises(ValueError, match="does not occur"):
        item.checked_api({"brief-1": "Unrelated document"}, MOCK_URL)
    item.openapi["paths"]["/invented"] = copy.deepcopy(API["paths"]["/lookup"])
    with pytest.raises(ValueError, match="source evidence"):
        item.checked_api({"brief-1": SOURCE}, MOCK_URL)
    item.unresolved = ["Response contract is missing."]
    with pytest.raises(ValueError, match="unresolved"):
        item.checked_api({"brief-1": SOURCE}, MOCK_URL)


def test_folder_entry_without_explicit_api_preserves_documents_and_mock_url(tmp_path):
    (tmp_path / "contract.md").write_text(SOURCE)
    inputs = load_inputs([tmp_path], None, mock_url=MOCK_URL)
    assert inputs.api == {}
    assert inputs.sources == {"brief-1": SOURCE}
    assert inputs.mock_url == MOCK_URL


def test_document_contract_policy_and_workflow_share_a_fresh_recorded_run(tmp_path):
    policy = Policy(rules=[Rule(
        id="lookup", requirement="Use the supplied lookup contract.", applies_when="Lookup",
        implementation="API tool", evidence=[
            Evidence(source="brief-1", quote=SOURCE),
            Evidence(source="deployment-config", quote=MOCK_URL),
        ],
    )])
    draft = WorkflowBuild(workflow={
        "initial_node": "lookup", "global_nodes": {}, "nodes": {"lookup": {
            "node_type": "conversation", "task_message": "Ask for the reference.",
            "tools": [{"type": "function", "function": {
                "name": "lookup", "handler": "__function__:api_call",
                "parameters": {"type": "object", "properties": {
                    "url": {"type": "string", "default": MOCK_URL + "/lookup"},
                    "method": {"type": "string", "default": "POST"},
                    "body": {"type": "object", "properties": {
                        "reference": {"type": "string"},
                    }},
                }},
            }}],
        }},
    }, rule_nodes={"lookup": ["lookup"]})
    outputs = iter([
        contract().model_dump_json(), policy.model_dump_json(), draft.model_dump_json(),
    ])
    prompts = []

    def respond(messages, _info):
        prompts.append("\n".join(
            str(part.content) for message in messages for part in message.parts
            if isinstance(part, UserPromptPart)
        ))
        return ModelResponse(parts=[TextPart(next(outputs))])

    @asynccontextmanager
    async def factory(_name, _seconds, _reasoning):
        yield FunctionModel(respond)

    inputs = Inputs(sources={"brief-1": SOURCE}, api={}, references="", manifest=[],
                    mock_url=MOCK_URL)
    out = tmp_path / "build"
    builder = Builder(inputs, out, model_factory=factory)
    asyncio.run(builder.run())
    trace = [json.loads(row) for row in (out / "trace.jsonl").read_text().splitlines()]
    starts = [row for row in trace if row["event"] == "model_call_start"]
    assert [row["purpose"] for row in starts] == ["service_contract", "policy", "workflow"]
    assert (out / "derived-openapi.json").exists()
    assert (out / "workflow.json").exists()
    assert builder.inputs.api["servers"] == [{"url": MOCK_URL}]
    # The explicit runtime URL is an input; model-derived API content is not policy evidence.
    assert "deployment-tool-api" not in prompts[1]
    assert "deployment-tool-api" in prompts[2]
    assert "deployment-config" in prompts[1]
    assert MOCK_URL in prompts[1]
    assert MOCK_URL in prompts[2]
    assert json.loads((out / "deployment-config.json").read_text()) == {"mock_url": MOCK_URL}
    policy.check_sources(builder.inputs.sources)
    assert trace[-1]["event"] == "builder_end"
    assert trace[-1]["status"] == "locally_validated"
