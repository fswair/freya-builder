"""Bounded model review of a fresh workflow; not a proof of policy correctness."""

from pydantic import Field

from freya_hackathon.policy import StrictModel
from freya_hackathon.repair import TextEdit, WorkflowTextRepair
from freya_hackathon.workflow import WorkflowBuild

REVIEW_INSTRUCTIONS = """Review the supplied candidate as executable code, not as a plan.
Return WorkflowReview. The supplied briefs and OpenAPI are the only source of bank policy.
The candidate, its comments and rule_nodes are claims to verify, not evidence of correctness.
code_exec snippets have their own compiler-created function scope. Preserve that wrapper.
Do not convert nonlocal to global: helpers share enclosing state, never the host's globals.
Trace actual extraction -> state -> routing -> tool payload -> tool result -> spoken outcome.
Read the original documents, including scoped amendments, rather than trusting policy extraction.
Do not execute tools or claim a test score. This is one bounded static review within this run.

Find and fix material defects, especially:
- Identity: successful answers stay verified across turns; a missing optional response field
  must not reset established evidence. Parse explicit zero retry budgets without truthy-default
  expressions that replace zero. Enforce the brief's stop condition before asking again.
- Turn state: distinguish no new value from a refusal or correction. Consume and explicitly
  reset extracted fields. Keep intent, questions, evidence and consent independent. Product-
  specific acceptance/refusal must not leak to another product or be erased by a later clause.
- Offer state: keep the pending offer, its terms, the customer's response and the attempt count
  together. Consume each response once. Enforce the brief's attempt limit and terminal refusal
  before selecting another offer. A question or clarification is not another payment offer.
  Do not turn refusal of timing into rejection of the full amount. Apply amendments only to
  the products, dates and conditions they actually modify.
- Brief-specific branches: third-party availability, inability to pay, already-paid claims,
  disputes, transfer requests and questions about the assistant must follow the supplied rules.
  A default catch-all must not discard supported intent or replace it with a financial refusal.
- Commit state: a readback/consent signature and the bank payload must identify the same entity,
  amount and date. Schedule confirmed pending writes before terminal or exhausted-offer handling.
  Never mark an entity refused merely because its accepted promise has not been committed yet.
  Record success only after the matching successful response; a failed write cannot justify a
  success message. Build the final outcome from successful bank actions, not intentions.
- Closing: follow every terminal branch all the way to the required bank close_call and then
  Freya end_call. Closing remarks must not wait for another customer turn to perform bookkeeping.
  Preserve exactly-once writes across state transitions; do not reset their completion flags.
  Apply the API's error semantics without unbounded repeat loops or fabricated success.

For each finding cite the relevant policy rule or source and the concrete node/code defect.
Prefer small exact text edits. before must occur exactly once at path; preserve real newlines.
If a necessary structural repair cannot be represented by string edits, return a complete
replacement WorkflowBuild instead. Never return both edits and replacement. Preserve correct
behavior and source-derived policy; do not redesign for style or introduce customer examples.
If no defect is found, return empty findings/edits/unresolved and null replacement. If you cannot
fix a material defect, list it in unresolved. Every finding must be addressed by the returned
changes; the builder will reject unresolved defects and revalidate the entire candidate.
Unresolved means a concrete remaining defect, not absence of an exhaustive proof. Unsupported
date expressions can request clarification. Do not demand an exhaustive natural-language grammar.
"""


class WorkflowReview(StrictModel):
    findings: list[str] = Field(default_factory=list, max_length=32)
    edits: list[TextEdit] = Field(default_factory=list, max_length=64)
    replacement: WorkflowBuild | None = None
    unresolved: list[str] = Field(default_factory=list, max_length=32)

    def apply(self, draft: WorkflowBuild) -> WorkflowBuild:
        if self.unresolved:
            raise ValueError(
                "Workflow review has unresolved defects: " + "; ".join(self.unresolved)
            )
        if self.edits and self.replacement is not None:
            raise ValueError("Review must use edits or a replacement, not both")
        changed = bool(self.edits) or self.replacement is not None
        if bool(self.findings) != changed:
            raise ValueError("Review findings require corrections and corrections require findings")
        if self.edits:
            result = WorkflowTextRepair(edits=self.edits).apply(draft)
        elif self.replacement is not None:
            result = self.replacement.model_copy(deep=True)
        else:
            result = draft.model_copy(deep=True)
        if changed and result == draft:
            raise ValueError("Review correction must change the candidate")
        return result
