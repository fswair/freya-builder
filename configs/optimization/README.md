# Optimization configurations

Run these from the repository root. Paths inside a configuration resolve relative
to that configuration's directory; the `../../` prefixes are intentional.

```bash
uv run --locked freya-optimize validate configs/optimization/example.json
uv run --locked freya-optimize run configs/optimization/example.json --out runs/gepa-study-001
```

`example.json` illustrates the public A/B development harness. `bounded*.json`
uses the predefined priority training subset with tighter search limits.
`references-v2.json`, `deterministic-reference.json` and `training-first-*.json`
preserve development experiment settings and their matching reference workflows.
Their presence does not imply that every experiment completed or improved results.

These are practice optimization inputs, not final-case policies. Final generation
receives its case directory, deployed mock URL and selected general guidance
directly through `diagnostics/finalize_submission.py`.
