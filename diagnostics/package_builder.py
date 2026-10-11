"""Create a local, allowlisted builder source bundle without credentials or datasets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RUNBOOK = """Freya builder source bundle — development preparation

This is a reproducible builder package, not an official hackathon submission and
not a claim about hidden-test quality. Final briefs must be supplied as inputs.
No customer datasets, historical runs, authentication files or account identifiers
are included. The two public development references are implementation examples;
the supplied brief and OpenAPI remain authoritative.

Requirements: Python 3.13, uv, the user's existing Codex authentication, and a Freya
API key configured locally. Create a local .env with FREYA_API_KEY and set a stable
FREYA_BANK_TEAM_KEY (at least 16 characters) if practice is needed. Do not share secrets.

Install/check imports:
  uv sync --locked
  uv run --locked python diagnostics/finalize_submission.py --help

After the final folder opens, deploy the mock from its service contract first.
Then generate, publish and export one agent with one command:
  uv run --locked python diagnostics/finalize_submission.py CASE_FOLDER \\
    --mock-url https://YOUR_DEPLOYED_MOCK.workers.dev \\
    --guidance benchmarks/builder-guidance.txt --slot FINAL_SLOT --out runs/FINAL_RUN

An explicit OpenAPI file may be supplied as the second positional argument. If it
is absent, the builder extracts a source-backed contract from the folder. All API
server scopes are bound to --mock-url. No bank service details are written into
the builder. For development, optional --workflow-reference arguments can select
benchmarks/development-references-v1/single-product.json and multi-product.json.
References are never authoritative for a new brief's language or business rules.

Repeat --additional-brief for each additional document belonging to that input.
Brief arguments accept UTF-8 text, DOCX, XLSX/XLSM and text PDFs, or directories.
Directory documents are loaded
recursively in sorted relative-path order; dotfiles are excluded. Identical paths
are included once, and every included file keeps its source label and byte hash.
Word tables and notes, Excel sheets/formulas/cached values, and PDF page boundaries
are preserved. Image-only PDF pages require OCR text; legacy DOC/XLS need conversion.
Unreadable attachments stop the build with an explicit error; they are not skipped.
Use a fresh output directory and the correct brief's API. No manual edits to the
generated workflow. The command generates, validates, publishes and exports the
workflow, with a matching trace and a 30-minute budget. It starts no simulations
or telephone calls. It does not send the official competition submission.

Inspect submission.json, submission.workflow.json and trace.jsonl in the output.
submission_ready verifies the export/trace package only, not conversational quality.
Preserve native Autobench records and model-call artifacts from the same fresh run.
The manifest pins every bundled file and the selected guidance. The guidance's
behavioral selection evidence is recorded separately in the development report.
"""


def build_bundle(
    root: Path, guidance: Path, output: Path, *, reference_version: str = "v1"
) -> dict:
    """Archive only explicit source surfaces; refuse collisions and symlink escapes."""
    root = root.resolve()
    if output.exists():
        raise ValueError("Bundle output already exists")
    if guidance.is_symlink() or not guidance.is_file():
        raise ValueError("Guidance must be a regular file")
    guidance_bytes = guidance.read_bytes()
    if not guidance_bytes.strip() or len(guidance_bytes.decode()) > 12_000:
        raise ValueError("Guidance must be nonempty and at most 12,000 characters")
    if not re.fullmatch(r"v[1-9][0-9]*", reference_version):
        raise ValueError("Invalid reference version")
    reference_directory = f"benchmarks/development-references-{reference_version}"
    if not any((root / reference_directory).glob("*.json")):
        raise ValueError("Selected reference directory has no workflows")
    paths = [
        root / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            "README.md",
            "diagnostics/finalize_submission.py",
            "diagnostics/package_builder.py",
            "vendor/freya-v3/README.md",
            "vendor/freya-v3/VERSION",
            "vendor/freya-v3/freya.py",
        )
    ]
    for directory, pattern in (
        ("src/freya_hackathon", "*.py"),
        ("benchmarks", "*.yaml"),
        (reference_directory, "*.json"),
        ("vendor/freya-v3/docs", "*.md"),
        ("vendor/freya-v3/examples", "*.json"),
        ("vendor/freya-v3/tools", "*.json"),
        ("vendor/freya-v3/tools", "*.py"),
        ("vendor/freya-v3/briefs", "*.md"),
    ):
        paths.extend(
            p
            for p in (root / directory).glob(pattern)
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        )
    contents: dict[str, bytes] = {}
    for path in sorted(set(paths)):
        relative = path.relative_to(root)
        if (
            not path.is_file()
            or any(part.startswith(".") for part in relative.parts)
            and relative.as_posix() != ".python-version"
            or path.is_symlink()
            or path.resolve() != path
        ):
            raise ValueError(f"Unsafe or missing bundle input: {relative}")
        contents[relative.as_posix()] = path.read_bytes()
    contents["benchmarks/builder-guidance.txt"] = guidance_bytes
    contents["RUNBOOK.txt"] = RUNBOOK.replace(
        "benchmarks/development-references-v1/", reference_directory + "/"
    ).encode()
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "scope": "builder source and public kit; no final brief run or quality claim",
        "official_submission": False,
        "reference_version": reference_version,
        "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in sorted(contents.items())
        },
    }
    contents["bundle-manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(contents.items()):
            archive.writestr(name, data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Bundle CRC verification failed")
        for name, expected in manifest["files"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected["sha256"]:
                raise ValueError("Bundle hash verification failed")
    return {
        "path": str(output),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "files": len(contents),
        "bytes": output.stat().st_size,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guidance", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reference-version", default="v1")
    args = parser.parse_args()
    print(
        json.dumps(
            build_bundle(ROOT, args.guidance, args.out, reference_version=args.reference_version)
        )
    )
