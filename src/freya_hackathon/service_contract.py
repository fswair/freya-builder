"""Extract a tool contract from supplied documents and bind it to an explicit mock URL."""

from __future__ import annotations

import copy
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field

from freya_hackathon.policy import Evidence, StrictModel
from freya_hackathon.workflow import api_operations, resolve

CONTRACT_INSTRUCTIONS = """Read the supplied documents and extract the bank service contract.
Return an OpenAPI 3 JSON object and exact source evidence for every HTTP operation. Preserve
the documented request fields, required fields, response envelopes, enums, error responses,
authentication mechanism and paths. Include every documented bank operation. Do not infer a
service contract from example conversations or import endpoints/fields from another project.
Use only the explicitly supplied mock base URL for servers. Do not contact a service.
The mock base URL is deployment configuration, not a source of bank policy. If the documents
do not define a necessary contract detail, put it in unresolved rather than inventing it.
Unresolved is scoped to a missing HTTP contract detail that prevents faithfully specifying a
documented operation, such as an undefined endpoint, required input schema or response field.
This stage specifies the available API; it does not prove that every business requirement can
be implemented by that API. Keep workflow-level decisions, unavailable business state and
platform integration questions in notes for the later policy/workflow stages. A choice among
documented request values is a workflow decision, not an undefined HTTP schema. These notes
must preserve the limitation; they do not authorize inventing fields, values or business rules.
Merely unspecified
optional constraints are notes: response examples do not imply exhaustive enums, and an absent
invalid-input response does not prevent sending a documented valid request. Preserve such
uncertainties in notes without inventing restrictions. Do not remove a genuinely missing
required HTTP contract detail just to pass a validation retry; new source evidence is required.
The evidence source must be a supplied document ID and the quote must occur literally in it.
Record whether request fields are strings, numbers, dates or enums exactly as documented.
This stage describes the API; it must not encode business rules as API defaults or customer
fixtures. The subsequent policy and workflow stages implement the source requirements.
"""


def validate_mock_url(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or "{" in url
        or "}" in url
    ):
        raise ValueError("Mock URL must be an explicit HTTPS base URL without credentials or query")
    return url.rstrip("/")


def bind_mock_url(api: dict[str, Any], mock_url: str) -> dict[str, Any]:
    """Override all OpenAPI server scopes, without mutating the supplied source object."""
    url = validate_mock_url(mock_url)
    result = copy.deepcopy(api)
    if not isinstance(result.get("paths"), dict):
        raise ValueError("OpenAPI requires a paths object")
    result["servers"] = [{"url": url}]
    for path, raw_item in result.get("paths", {}).items():
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("OpenAPI operation paths must start with /")
        if not isinstance(raw_item, dict):
            raise ValueError("OpenAPI path item must be an object")
        item = resolve(raw_item, result)
        if not isinstance(item, dict):
            raise ValueError("OpenAPI path item must be an object")
        result["paths"][path] = item
        item.pop("servers", None)
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            if method in item:
                if not isinstance(item[method], dict):
                    raise ValueError("OpenAPI operation must be an object")
                item[method].pop("servers", None)
    api_operations(result)
    return result


class OperationEvidence(StrictModel):
    method: str
    path: str
    evidence: list[Evidence] = Field(min_length=1)


class ServiceContract(StrictModel):
    openapi: dict[str, Any]
    operations: list[OperationEvidence] = Field(min_length=1)
    unresolved: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def checked_api(self, sources: dict[str, str], mock_url: str) -> dict[str, Any]:
        if self.unresolved:
            message = "; ".join(self.unresolved)
            raise ValueError("Service contract has unresolved details: " + message)
        result = bind_mock_url(self.openapi, mock_url)
        declared = {
            (method.upper(), path)
            for path, item in result["paths"].items()
            for method in item
            if method in {"get", "post", "put", "patch", "delete", "head", "options"}
        }
        supported = {(op.method.upper(), op.path) for op in self.operations}
        if supported != declared or len(supported) != len(self.operations):
            raise ValueError("Every service operation needs unique source evidence")
        for operation in self.operations:
            for evidence in operation.evidence:
                quote = " ".join(evidence.quote.split())
                document = " ".join(sources.get(evidence.source, "").split())
                if not quote or quote not in document:
                    raise ValueError("Service contract evidence does not occur in its source")
        return result
