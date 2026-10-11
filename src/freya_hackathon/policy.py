"""Source-backed intermediate representation; no bank-specific rules live here."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(StrictModel):
    source: str = Field(description="Input document ID, such as brief-1")
    quote: str = Field(min_length=1, description="Exact supporting text from that document")


class Rule(StrictModel):
    id: str = Field(min_length=1)
    requirement: str = Field(min_length=1)
    applies_when: str = Field(min_length=1)
    implementation: str = Field(description="Proposed code, routing or dialogue enforcement")
    evidence: list[Evidence] = Field(min_length=1)
    overrides: list[str] = Field(default_factory=list, description="IDs of superseded rules")


class Policy(StrictModel):
    rules: list[Rule] = Field(min_length=1)
    unresolved: list[str] = Field(
        default_factory=list,
        description="Unresolved contradictions or missing authoritative source dependencies. "
        "Do not clear a missing dependency merely to pass validation.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Source-documented limitations or explicitly deferred decisions. Preserve "
        "their scope and the source-backed handling rule; notes cannot resolve missing policy.",
    )

    def check_sources(self, sources: dict[str, str]) -> None:
        ids = [rule.id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("Rule IDs must be unique")
        for rule in self.rules:
            for evidence in rule.evidence:
                if evidence.source not in sources:
                    raise ValueError(f"{rule.id}: unknown evidence source {evidence.source}")
                quote = " ".join(evidence.quote.split())
                document = " ".join(sources[evidence.source].split())
                if not quote or quote not in document:
                    raise ValueError(f"{rule.id}: evidence quote does not occur in source")
            if rule.id in rule.overrides or set(rule.overrides) - set(ids):
                raise ValueError(f"{rule.id}: invalid overridden rule IDs")
        # Reject amendment cycles instead of silently choosing a policy.
        graph = {rule.id: rule.overrides for rule in self.rules}

        def visit(node: str, active: set[str], done: set[str]) -> None:
            if node in active:
                raise ValueError("Cyclic policy overrides")
            if node in done:
                return
            active.add(node)
            for other in graph[node]:
                visit(other, active, done)
            active.remove(node)
            done.add(node)

        done: set[str] = set()
        for node in graph:
            visit(node, set(), done)
        if self.unresolved:
            raise ValueError("Policy has unresolved requirements: " + "; ".join(self.unresolved))
