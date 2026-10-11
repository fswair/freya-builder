FREYA DATASET v2 — proposed tests, not execution results

CONTENTS
live/: 48 additional customer scripts tied to registered public bank fixtures.
32 train / 8 validation / 8 holdout; 24 scenario groups with two speaking variants.
Brief A: 24 cases. Brief B: 24 cases. The manifest supplies 16 priority TRAINING IDs.

generalization/: six original synthetic brief families, four cases per family.
Each split contains one banking and one insurance bundle (8 local cases per split).
All six use extended_local_contract APIs, not the announced official final surface.
Three are clean specifications and three are email/policy/amendment/code bundles.

sources/: exact copies of the supplied source kit documents, including its scope notes.
request/: the original dataset instructions, retained for audit.
review.txt: what was checked and what was not, without held-out scripts or answers.
validation-report.json: aggregate machine-check results; case detail stays in its split.
schemas/: the local JSON schemas used by the artifact checker.
validate.py + validation_support.py: offline artifact and proof-witness checkers.
These are NOT a phone agent, a builder, a simulator, or an HTTP mock adapter.

LIVE INTEGRATION BOUNDARY
Use practice_session_case (PUB-01..PUB-13) to choose a registered bank seed.
Never use the DEV case ID to start the bank session. The bank supplies the runtime
customer_id; it is not pre-generated in these files. Preserve variables and all
fixture-specific fields. Existing faults are source evidence, not a new injection.
Use script for the simulated customer. Treat expected/checks/metadata as evaluator-only.
Never merge the full case object into workflow test_variables.

The supplied package does not establish an endpoint for registering new personas.
Using these candidates needs a practice harness that can substitute the customer
script while keeping the selected PUB bank fixture. This runner capability has not
been verified, and these files are not advertised as a drop-in run_cases.py import.

LOCAL INTEGRATION BOUNDARY
Each bundle's manifest.source_files is the ONLY builder input for that bundle.
manifest.evaluator_files are excluded: fixture, cases, private answers, oracle,
author review and validation results. The runtime adapter loads the selected
case's fresh initial_state; the builder must not inspect it. A customer simulator
receives that case's customer_private_facts and script, not its oracle.

Implement local tools from openapi.json and mock-fixture.json before any execution.
The .invalid URLs are reserved non-live targets; do not send these cases to Freya.
No generalization case has practice_session_case. The declarative operation models
specify ordered guards, atomic effects, explicit failures and repeat behavior.
The offline checker can evaluate an authored feasibility witness through those
rules, but it does not instantiate a server or generate a conversation.

ORACLE SEMANTICS
Live expected arrays retain the public nested shapes. Compare product-keyed ledger
contents without adding arbitrary array-order requirements; check policy-required
tool/conversation ordering separately. A null reason in the expected ledger means
omit reason from the close_call payload for promise/paid, not send a JSON null.
Generalization oracle.required_actions uses specified payload fields and bindings;
its concrete witness request keys are examples, not compulsory values. Its witness
is one permitted path. Partial-order constraints are conditional on the relevant
business/conversation event and do not require an irrelevant event to occur.
A tool request containing confirmed=true does NOT prove spoken customer consent.

SPLIT PROTOCOL
Training supplies optimizer evidence. Validation selects candidates. Holdout is
reserved until selection and never enters reflection or repair. Keep scenario
pairs and brief families intact. The authoring QA of held-out artifacts reported
here is not a builder evaluation or evidence of a held-out success rate.
Do not index this entire ZIP as builder context. Whitelist source_files for the
selected brief and expose only the intended training/evaluation split.

REPRODUCING ARTIFACT CHECKS
Use Python 3.10+ and the jsonschema dependency in requirements.txt.
  python validate.py
This checks train + validation without opening held-out case or policy content.
The manifests may still expose held-out IDs, paths and counts, as permitted.
For dataset-authoring QA or after candidate selection only:
  python validate.py --include-holdout
Reports are regenerated. It does not call any external API or run a customer.
The optional FastAPI OpenAPI model check runs when FastAPI is installed.

LIMITATIONS
No Freya calls, builder/GEPA runs, live/mock conversations, local adapter execution,
three-repetition success measurement or official score is provided. JSON Schema
and OpenAPI structural checks do not equal a full OpenAPI conformance certification.
Text overlap checks and author review do not mathematically prove semantic
independence or prevent future misuse of holdout. SHA256SUMS.txt covers delivered
files other than itself; all data and policies here are synthetic or supplied.
