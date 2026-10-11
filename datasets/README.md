# Development datasets

`freya-v2/` contains synthetic development cases and copies of the public starter
materials. These are proposed tests and dataset-authoring checks, not Freya scores.
The original [dataset README](freya-v2/README.txt) describes schemas and oracles.

| Path | Purpose |
| --- | --- |
| `freya-v2/sources/` | Public briefs, OpenAPI, scope notes and the 13 public cases |
| `freya-v2/live/` | 32 training and 8 validation scripts tied to public fixtures |
| `freya-v2/live-priority/` | 16 manifest-selected training cases, unchanged validation and provenance |
| `freya-v2/generalization/` | Synthetic banking/insurance train and validation bundles for local mocks |
| `freya-v2/schemas/` | Dataset validation schemas |
| `freya-v2/request/` | Original dataset-authoring instructions |
| `freya-v2/source.zip` | Unmodified original package, including sealed holdout payloads |

The archive SHA-256 is
`1e16e0a5fc89c02efe7598d848f74edd9a0233824b8c4317866d0ced97d040cf`.
The import receipt records extracted and sealed paths. Holdout case/policy contents
remain unextracted and are not used by development search. Packaging performs
credential byte scans without exposing cases to the builder or evaluation harness.

Verify optimizer inputs without external calls or opening holdout:

```bash
uv run --locked freya-optimize validate configs/optimization/example.json
```

To re-import train/validation into a new directory while keeping holdout sealed:

```bash
uv run --locked python diagnostics/import_dataset.py \
  datasets/freya-v2/source.zip --out runs/dataset-reimport
```

The author's `validate.py` checks artifact schemas; its dependencies are listed in
`freya-v2/requirements.txt`. It does not simulate customers. Preserve original
source bytes so their recorded hashes remain useful.

Generalization bundles use local-only APIs and need their own mock adapter. Give
the builder only each manifest's `source_files`. Fixture state, customer scripts,
private facts, expected results and oracles belong to evaluation. Never index the
whole archive as builder context. Provision holdout only for separate evaluation
after candidate selection; do not use it for reflection or repair.

Some provenance files retain original local paths as historical metadata. These
are not runtime dependencies. Active configs use repository-relative paths and
do not require the author's `documents/` directory.
