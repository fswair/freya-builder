# Final submission

The three entry files are `submission.json`, `final.workflow.json`, and
`final.trace.jsonl`. The workflow is an unchanged `freya.export_workflow` export;
the trace comes from the same builder run. No workflow was edited by hand.

The `commit` field names the fixed Git tag `freya-final-20261011-v2`. Resolve its full
commit hash with `git rev-parse freya-final-20261011-v2^{commit}`. The tag points to the
commit containing these exact files. Using a tag avoids embedding a commit's own
hash inside its contents. The resolved hash is also supplied in the entry email.

## Reproduction

Follow the root README for Python, `uv`, Freya credentials, and builder-model
authentication. Install the locked environment with `uv sync --locked`, then run
the `run_command` from `submission.json`, replacing `{case_folder}` with the full
case directory and `{mock_url}` with the declared live Worker URL. Use a fresh
output directory. The command generates and exports a workflow without manual
intervention. It does not place a telephone call or send an entry email.

The live mock is implemented separately in `mock-worker/`; its supplied synthetic
response table is versioned there. Keep the Worker running until judging ends.

## Evidence

`packet-check.json` records the original builder source revision and SHA-256
hashes of the two unchanged run artifacts. `provenance/` contains the full model
messages, input manifest, extracted contract and policy, rule-to-node mapping,
generation instructions, and validation results. `provenance/export-report.json`
is the internal export report; it is distinct from the official `submission.json`.

Structural validation and a successful export do not establish behavioral
correctness. Any separate synthetic evaluation report is explicitly marked as
unofficial and includes its evidence limitations. Local native Autobench records
are retained separately; they are not required email attachments.
