# Freya — Build the Builder

A source-backed builder that turns a case folder and a deployed bank-service mock
URL into a Freya phone-agent workflow. Pydantic AI extracts policy and generates
the workflow; a compiler and validators check the result. Autobench records model
calls, HTTP activity and evaluation evidence. Pydantic GEPA optimizes general
builder guidance during development.

The final entry files and matching generation evidence are in
[`submission/`](submission/README.md). The [candidate review](submission/REVIEW.md)
records the separate synthetic checks and their limitations.

Python 3.13 and [uv](https://docs.astral.sh/uv/) are required. The main environment
uses published packages: Autobench 0.4.0, pydantic-gepa 0.1.1 and GEPA 0.1.4.
It does not require sibling repositories or editable package installations.

## Setup

```bash
uv sync --locked
uv run --locked pytest
make smoke RECORD=runs/stack-check
```

Tests and the stack smoke test do not call external APIs. Historical regression
tests may skip when their private run artifacts are absent. `make check` also runs
Ruff and type checks. Each example output directory must be new.

The builder uses `codex-auth-helper==1.10.1` with an existing authenticated
`~/.codex/auth.json` session. Its default model is `gpt-6-astra`, reasoning `high`.
Configure generation with `--model`, `--reasoning`, `--seconds`, `--call-seconds`
and `--attempts`. Local generation calls the builder model; offline tests do not.

For Freya validation, publication, export or synthetic practice, copy
[.env.example](.env.example) to `.env` and set `FREYA_API_KEY`. Set a persistent
`FREYA_BANK_TEAM_KEY` or let the practice client create and reuse the ignored
`.freya-team-key` file. Do not commit credentials.

`environments/local/` and the `*-local` Make targets are optional package-development
tools. They require the sibling checkouts listed in their requirements file and
are not used by the standard installation or final builder command.

## Final case: folder + mock URL

The [official hackathon page](https://www.freyavoice.ai/hackathon#submit) is the
source of current rules and supersedes older format descriptions in the starter
kit. The October 11 final format has one Turkish credit-card-statement case,
released at 17:00 UTC, with submissions closing at 20:00 UTC. The team implements
the supplied bank-service contract as a separate Cloudflare Worker and deploys it
before running the builder. Final case rules, values and names remain runtime
inputs rather than additions to builder source or fixed prompts.

The [mock-worker scaffold](mock-worker/README.txt) is not a working bank mock yet.
Implement the released contract, test its customer records, deploy it, and keep
the service available through the organizers' evaluation.

```bash
uv run --locked python diagnostics/finalize_submission.py \
  path/to/case-folder \
  --mock-url https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev \
  --guidance benchmarks/builder-guidance-v3.txt \
  --slot final --out runs/final-001
```

This performs fresh generation, platform validation, publication and official
workflow export. It does not start a simulation, place a phone call or send an
entry. A second positional OpenAPI path is optional; otherwise, the builder
extracts the service contract from the input documents with supporting source
evidence. The explicit mock URL binds service operations to the deployed mock.

Successful output includes `submission.workflow.json`, the matching `trace.jsonl`,
and an internal `submission.json` provenance report. Export parity, file hashes,
platform IDs and elapsed time are checked. `submission_ready` means the export
package is valid, not that it has passed customer scenarios.

**The generated `runs/.../submission.json` is not the official entry form.** Use
[the template](diagnostics/final-submission-template.json) and
[entry instructions](diagnostics/final-submission-instructions.txt) to prepare the
eight-field form. Commit the builder, mock source, exact exported workflow and
matching credential-free trace. Organizers must be able to read the declared
repository and commit. Do not hand-edit the workflow. Official submission is a
separate action. The default 1,800-second finalizer budget is a local engineering
limit, not an additional official time-limit rule.

## Local generation with the public brief

The selected general guidance is
[builder-guidance-v3.txt](benchmarks/builder-guidance-v3.txt). The vendored starter
kit supplies public briefs, API definitions, documentation and examples.

```bash
uv run --locked freya-builder \
  vendor/freya-v3/briefs/brief-a.md \
  vendor/freya-v3/tools/openapi.json \
  --guidance benchmarks/builder-guidance-v3.txt \
  --out runs/brief-a-001
```

Brief B extends A: add
`--additional-brief vendor/freya-v3/briefs/brief-b.md`. Without practice options,
this command does not call Freya or the bank API.

Supported inputs are text/Markdown, DOCX, XLSX/XLSM and text-bearing PDF. The reader
preserves tables and notes, worksheet boundaries, formulas and cached values,
PDF page boundaries and original-byte hashes. Scanned PDFs need OCR; legacy
DOC/XLS files need conversion. Unreadable input fails explicitly.

Policy extraction records source quotations and rule relationships. Local checks
reject invalid evidence and unresolved conflicts. Workflow generation then uses
the source documents, policy and Freya references. Service-contract extraction
runs first when needed. Invalid candidates receive bounded feedback within the
remaining time budget.

The compiler handles Freya variable persistence, response mappings, extraction
resets and Python function scope. Checks cover transitions, tools, API addresses,
required fields and enums. Syntax checks do not execute generated Python. Local
validation does not prove runtime correctness or platform acceptance.

Artifacts include `policy.json`, `workflow.json`, `rule-nodes.json`, `validation.json`,
`inputs.json`, `trace.jsonl` and model-message records. Failed candidates remain
available for inspection; the final workflow is written only after local checks.
Default generation limits are 1,200 seconds overall, two attempts per stage and
600 seconds per model call, bounded by the remaining total budget.

## Datasets and reproducibility

[datasets/](datasets/README.md) contains development cases, public source documents,
schemas, validation tools, provenance and the original dataset archive. Train and
validation files are checked in. Holdout payloads remain unextracted inside
`source.zip`; optimizer search and validation commands do not read them.

Optimizer configurations and the offline integration fixture use
`datasets/freya-v2/sources/public-test-set.json`. They do not depend on the local
`documents/` directory, which contains private notes and raw dashboard captures.
`runs/` also stays local; chosen submission artifacts must be committed separately.

Synthetic generalization bundles require their own local mock adapter and cannot
run against the public practice bank. Their oracles and customer-private facts
are evaluator inputs, never builder inputs. Follow each manifest's source-file
allowlist and preserve the train/validation/holdout separation.

## Synthetic Freya practice

```bash
uv run --locked python -m freya_hackathon.practice runs/brief-a-001 \
  --cases datasets/freya-v2/sources/public-test-set.json --brief A
```

This publishes the generated workflow and starts a synthetic `mode=chat`
simulation. It does not place a real phone call. Run only one simulation per
workspace. `--only PUB-01,PUB-02` selects public cases. The public bank supplies
PUB-01 through PUB-13; arbitrary new bank customers are unsupported.

Results, conversations, bank logs and exports are written to `practice/`. Platform
IDs enter the run trace. To collect an existing simulation without starting another:

```bash
uv run --locked python -m freya_hackathon.practice runs/brief-a-001 --collect
```

A local timeout does not cancel remote work: collect its results before starting
another run. `practice/runtime-health.json` preserves official scores separately
from infrastructure errors, unresolved runtime errors, simulator errors and
incomplete evidence. Affected runs cannot support clean model comparisons.
Public-case success is not evidence of hidden-case generalization.

## Optimization and offline checks

```bash
uv run --locked freya-optimize validate configs/optimization/example.json
uv run --locked python -m freya_hackathon.optimization_smoke \
  --out runs/gepa-environment-smoke-001
```

Both commands are offline. The integration smoke uses a Pydantic AI `FunctionModel`
and HTTPX `MockTransport` to exercise GEPA, recording and replay. It is not a Freya
behavioral evaluation. To start an actual development search:

```bash
uv run --locked freya-optimize run configs/optimization/example.json --out runs/gepa-study-001
```

GEPA changes general workflow-generation guidance, capped at 12,000 characters.
Briefs, service contracts, policy extraction, compiler, runtime model and scoring
stay fixed. References are development examples, not policy sources. Each metric
call evaluates one brief's batch, not one customer. The example uses one training
and three validation repetitions, with one simulation at a time. Infrastructure
or incomplete-evidence failures stop selection instead of reducing quality scores.

[Optimization configurations](configs/optimization/README.md) live in
`configs/optimization/`. Bounded configurations use the predefined priority training subset.
Config variants preserve experiments; they do not imply every search completed or
improved performance. Selected guidance does not automatically replace the default.
Holdout runs require frozen selection artifacts and explicitly provisioned holdout
data after selection. Never feed holdout results back into search or repair.

The `offline` module runs reviewed generated Python in a separate process with
mock bank responses and resource limits. Regression fixtures cover consent, dates,
partial failures and closing behavior. They do not prove spoken intent extraction
and are not a security sandbox for untrusted code. Repair/recompile commands are
development tools; final entries require a fresh builder run.

Brief-specific fixtures live in `freya_hackathon.development.practice.brief_a` and
`brief_b`, separate from the generic builder. Run their CLIs with `python -m`.
The top-level `practice` module is the shared Freya client and simulation runner;
it is not a Brief A/B policy implementation. Historical helpers in `diagnostics/`
may require local experiment artifacts; see [the tool notes](diagnostics/README.md).

## Traces and evidence

Builder, practice, collection and optimization commands create immutable Autobench
records. Native Pydantic AI, OpenAI, HTTPX and pydantic-gepa instrumentation captures
local operations, model usage, timing and errors. HTTP bodies and authentication
headers are not captured. Known credentials are removed from evidence copies;
original local files remain unchanged.

Freya's remote conversation/tool events are retained as artifacts, not represented
as locally instrumented model calls. Missing token or cost data is not invented.
Case records preserve official outcomes separately from runtime diagnostics.
Imported historical evidence is labeled as imported.

```bash
uv run --locked autobench replay PATH_TO_AUTOBENCH_RECORD
uv run --locked autobench report PATH_TO_AUTOBENCH_RECORD
uv run --locked python diagnostics/audit_recordings.py PATH_TO_AUTOBENCH_RECORD
```

Replay and reporting use recorded evidence without rerunning the subject or making
model/API calls. File hashes and evidence manifests preserve provenance.
