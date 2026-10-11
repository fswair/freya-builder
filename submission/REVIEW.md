# Final candidate review

This is the unmodified output of `runs/final-006`, generated with GPT-6 Astra
at high reasoning effort from the complete final case folder. The original
builder source is commit `018c4a355e67b477a638d54a48c63863c4dfe162`.

The workflow has 26 nodes and includes all seven supplied bank operations.
Freya validation, publication, export parity, and the packet's workflow/trace
hash checks passed. The builder source passed lint, formatting, type checking,
and 1,012 tests before this run. These are structural and engineering checks,
not evidence of a perfect behavioral score.

## Synthetic checks

The six cases were written from the supplied example scenarios. They are not
the organizers' hidden evaluation and are not an official score. The exact same
export was tested twice, with all results retained:

| Case | First run | Repeat |
| --- | --- | --- |
| FINAL-EX-01 | Fail | Pass |
| FINAL-EX-02 | Pass | Fail |
| FINAL-EX-03 | Fail | Fail |
| FINAL-EX-04 | Fail | Pass |
| FINAL-EX-05 | Fail | Fail |
| FINAL-EX-06 | Pass | Pass |
| Total | 2/6 | 3/6 |

Both result snapshots were stable within each run. All twelve call records
were retrieved. Both runtime-health reports found no observed
infrastructure, unresolved runtime, or simulator errors. The reported failures
are therefore retained as failures; they are not excluded or attributed to an
outage.

Observed weaknesses include interrupted or incomplete spoken summaries,
incorrect spoken amounts during card selection, and premature spoken claims
of a completed change. The generated workflow's presence of a guarded tool path
does not establish that every spoken answer obeys it. A statement that a change
occurred is also not, on its own, evidence that the change API was called.

The prior export supported only four bank operations and scored 2/6 in its
single synthetic run. This candidate preserves the requested change path and
the full service contract, but the two small runs do not establish a reliable
overall improvement or predict a hidden score.

The final case's unresolved source and integration questions remain documented
in `provenance/policy.json`; they have not been silently declared resolved.
At the recorded 19:32:26 UTC snapshot, two additional follow-up scenarios had
passed: a new card's future first statement date and cut-day total debt. The
other-card-before-offer scenario was still running. This is partial evidence,
not a completed 3-case score; see `evaluation/follow-up-progress.json`.
