"""Adapt a public development reference without regenerating its entire runtime."""

from copy import deepcopy
from typing import Any

from pydantic import Field

from freya_hackathon.policy import StrictModel
from freya_hackathon.repair import TextEdit, WorkflowTextRepair
from freya_hackathon.workflow import WorkflowBuild

ADAPTATION_INSTRUCTIONS = """Return WorkflowAdaptation, not a complete new workflow.
Choose one supplied development-reference ID whose capabilities fit the new brief. Its workflow
is a public development example, NOT bank policy. Read the actual supplied briefs and OpenAPI.
Keep proven node IDs, state encoding, extraction/reset mechanics, API result handling and closing
lifecycle where compatible. Do not redesign those mechanisms merely to restate the same policy.
Check that every reset value is accepted by its extraction enum and pattern; an invalid reset
does not clear the previous turn. Preserve a neutral, non-consenting reset contract.
Prefer the smallest source-required edits. New generic guidance is not a reason to rewrite a
compatible reference mechanism. Change an extraction representation only together with every
reader and writer; payment and callback date fields may have different existing contracts.
Preserve contextual acceptance of a pending offer: a short yes need not repeat its terms.
Keep uncertain decisions pending for clarification; do not turn missing extraction into a
terminal refusal. Preserve offer history and confirmed pending writes across question turns.
Audit consent gates as well as extraction descriptions: a bare accept enum with missing current
utterance evidence must never authorize financial writes. Keep valid short-answer confirmation
of the spoken, unchanged readback. Never default missing product scope to all when applying new
terms or mixed dispositions. Check existing paid-evidence guards for passive completed-payment
claims and clause-local negation/future handling; a narrow reference regex is not proven policy.
Preserve unprocessed scoped proposals across clarification. Partial extraction must not turn
substantive speech into a bare refusal or silently fall back to an earlier offer's terms.
Recompute missing-slot flags after a clarification answer; a supplied date must clear date
uncertainty without requiring an unrelated accept/reject label. Do not retain sticky incomplete
flags or build unlimited clarification loops. Keep current evidence separate from retained facts.
Do not require a normalized ISO date or numeric value to appear verbatim in speech: retain
traceable equivalent spoken terms and distinguish actual conflicts from repeated equivalent
representations. Audit new early-return guards so the source-required bounded fallback remains
reachable. Proposing source-backed terms is separate from authorizing their financial write.
Audit every policy-dependent constant, condition, quoted phrase, date rule, product rule, identity
field, outcome enum and API field against the new inputs. Change every mismatch; do not assume
that the reference's bank, calendar, fields or policy apply. Never select policy from a case ID.
Return exact string edits for prompts and code. before must occur once at its existing path.
Omit unchanged text edits; verified no-op edits do not alter the reference.
For structural changes, replacements replace an existing JSON value at a workflow path. To add
or remove a member, replace its containing object. All text edits run first, then replacements
in order. Return a complete rule_nodes mapping for the NEW policy IDs, not the reference IDs.
When retiring a source-specific node, remove it from the nodes object and the new rule mapping.
Do not leave unreachable placeholder nodes. A prohibition is implemented by the reachable
controller that prevents the action, not by an unreachable retired tool node.
An unchanged implementation is allowed only where the new source-backed policy agrees with it.
If required authoritative documents are missing or the adaptation cannot be completed, list the
blockers in unresolved. Do not return an empty edit list as a successful adaptation while saying
in the notes that it is blocked. A development reference is not a substitute for missing policy.
Explain which implementation mechanisms were preserved and which policy differences were applied
in adaptation_notes. These notes are model claims, not test results. Do not claim a score.
"""


class Replacement(StrictModel):
    path: list[str | int] = Field(min_length=2, description="Existing path starting with workflow")
    value: Any


class WorkflowAdaptation(StrictModel):
    reference_id: str
    adaptation_notes: list[str] = Field(min_length=1, max_length=32)
    edits: list[TextEdit] = Field(default_factory=list, max_length=64)
    replacements: list[Replacement] = Field(default_factory=list, max_length=32)
    rule_nodes: dict[str, list[str]]
    unresolved: list[str] = Field(
        default_factory=list,
        description="Missing authoritative sources or other blockers to completing adaptation. "
        "A blocked source-dependency report must populate this list, even with no edits.",
    )

    def apply(self, references: dict[str, dict]) -> WorkflowBuild:
        if self.unresolved:
            raise ValueError(
                "Adaptation has unresolved requirements: " + "; ".join(self.unresolved)
            )
        if self.reference_id not in references:
            raise ValueError("Unknown development reference ID")
        draft = WorkflowBuild(
            workflow=deepcopy(references[self.reference_id]), rule_nodes=deepcopy(self.rule_nodes)
        )
        if self.edits:
            if any(edit.path[0] != "workflow" for edit in self.edits):
                raise ValueError(
                    "Adaptation edits must target workflow; supply rule_nodes separately"
                )
            draft = WorkflowTextRepair(edits=self.edits).apply(draft, allow_noop=True)
        data = draft.model_dump()
        for replacement in self.replacements:
            if replacement.path[0] != "workflow":
                raise ValueError("Replacement must target workflow")
            target: Any = data
            try:
                for part in replacement.path:
                    if isinstance(target, list):
                        if type(part) is not int or not 0 <= part < len(target):
                            raise ValueError("Invalid replacement list index")
                    elif not isinstance(target, dict) or not isinstance(part, str):
                        raise ValueError("Invalid replacement object path")
                    parent, key = target, part
                    target = target[part]
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError("Replacement path does not exist") from exc
            parent[key] = deepcopy(replacement.value)
        return WorkflowBuild.model_validate(data)
