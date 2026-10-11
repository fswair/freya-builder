# Development and submission tools

These scripts fall into two groups. General tools accept input/output paths and
can run from this checkout. Historical probes reproduce specific local experiments
and therefore require the referenced private artifacts under `runs/` or ignored
diagnostic output paths. Those artifacts are not bundled or silently substituted.

General entry points include:

- `finalize_submission.py`: fresh generation, validation, publication and export.
- `run_custom_simulation.py`: evaluate an existing export using supplied synthetic
  personas in chat mode, without changing or redeploying the workflow. Cases are a
  JSON list with `id`, `title`, `script`, `checks` and `variables`. Omit `--execute`
  to validate the cases without contacting Freya. Results include two independent
  server snapshots, transcript evidence, runtime health and an Autobench record;
  these are diagnostic results, not the official competition score.
- `import_dataset.py`: import train/validation while leaving holdout sealed.
- `prepare_priority_dataset.py`: derive the predefined priority training subset.
- `audit_recordings.py`: check supplied local Autobench records without API calls.
- `prepare_git_source.py`: credential-check the explicit source/data allowlist.

Read each CLI's `--help` before use. Output directories generally must be new.
`prepare_priority_dataset.py` defaults to the already committed priority directory;
use its Python `prepare(source, output)` function with a new destination to recreate it.

Examples of historical dependencies include `measure_candidates.py` requiring
`current-candidates.json` and its local run directories,
`probe_a_restatement_safety.py` requiring its saved training snapshot, and
`package_iteration_evidence.py` requiring the two original service-response files.
Their absence does not affect builder generation or the standard test environment.

Public A/B regression fixture generators are in
`freya_hackathon.development.practice.brief_a` and `brief_b`. They test generated
controller behavior with scripted extractions; they are not hidden-case policy
and cannot establish conversational or official evaluation success.

Autobench development traces, raw conversations, dashboard captures and private
notes stay local. The final entry separately needs the exact generated workflow
and its matching sanitized builder trace, committed with the builder source.
See [the entry instructions](final-submission-instructions.txt).

JSON inputs use a compact text representation to reduce model context size.
Only whitespace outside strings is removed; numeric literals, escaping, repeated
keys and key order remain intact. The manifest hashes the original input bytes
and records when this representation was used. Original input files are not edited.
