"""Check an explicit source allowlist for known credentials, then optionally stage it."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

from freya_hackathon.builder import ROOT, write_json
from freya_hackathon.recording import secrets_to_redact


def prepare(*, stage: bool, report: Path) -> dict:
    files = {
        ROOT / name
        for name in (
            ".gitignore",
            ".env.example",
            ".python-version",
            "pyproject.toml",
            "uv.lock",
            "Makefile",
            "README.md",
            "datasets/README.md",
            "datasets/freya-v2/source.zip",
            "mock-worker/package.json",
            "mock-worker/package-lock.json",
            "mock-worker/wrangler.jsonc",
            "mock-worker/README.txt",
            "diagnostics/final-submission-template.json",
            "diagnostics/final-submission-instructions.txt",
            "diagnostics/README.md",
        )
    }
    for directory, pattern in (
        ("src", "**/*.py"),
        ("tests", "**/*.py"),
        ("tests/fixtures", "*.json"),
        ("benchmarks", "**/*.json"),
        ("benchmarks", "**/*.yaml"),
        ("benchmarks", "**/*.txt"),
        ("diagnostics", "*.py"),
        ("mock-worker/src", "**/*.js"),
        ("vendor/freya-v3", "README.md"),
        ("vendor/freya-v3", "VERSION"),
        ("vendor/freya-v3", "freya.py"),
        ("vendor/freya-v3/docs", "*.md"),
        ("vendor/freya-v3/briefs", "*.md"),
        ("vendor/freya-v3/examples", "*.json"),
        ("vendor/freya-v3/tools", "*.json"),
        ("vendor/freya-v3/tools", "*.py"),
        ("configs/optimization", "*.json"),
        ("configs/optimization", "README.md"),
        ("environments/local", "requirements.*"),
        ("datasets/freya-v2", "**/*.json"),
        ("datasets/freya-v2", "**/*.txt"),
        ("datasets/freya-v2", "**/*.md"),
        ("datasets/freya-v2", "**/*.py"),
    ):
        files.update((ROOT / directory).glob(pattern))
    credentials = {value.encode() for value in secrets_to_redact()}
    inventory = {}
    for path in sorted(files):
        relative = path.relative_to(ROOT).as_posix()
        if path.is_symlink() or not path.is_file() or path.resolve() != path:
            raise ValueError(f"Unsafe source path: {relative}")
        data = path.read_bytes()
        if any(secret in data for secret in credentials):
            raise ValueError(f"Known credential found in source candidate: {relative}")
        if path.suffix == ".zip":
            # Scan bytes only; do not expose sealed holdout cases to a model or harness.
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) > 500 or sum(item.file_size for item in members) > 25_000_000:
                    raise ValueError(f"Archive exceeds source scan limits: {relative}")
                for member in members:
                    if member.is_dir():
                        continue
                    member_data = archive.read(member)
                    if any(secret in member_data for secret in credentials):
                        raise ValueError(f"Known credential found in archive: {relative}")
        inventory[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    current = (
        subprocess.run(
            ["git", "-C", str(ROOT), "diff", "--cached", "--name-only", "-z"],
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .split("\0")
    )
    tracked_sources = (
        subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--", "src", "optimization*.json"],
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .split("\0")
    )
    staged_deletions = (
        subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "diff",
                "--cached",
                "--name-only",
                "--diff-filter=D",
                "--no-renames",
                "-z",
                "--",
                "src",
                "optimization*.json",
            ],
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .split("\0")
    )
    removed_sources = {
        path
        for path in tracked_sources + staged_deletions
        if (path.endswith(".py") or Path(path).match("optimization*.json"))
        and not (ROOT / path).exists()
    }
    if set(current) - set(inventory) - removed_sources - {""}:
        raise ValueError("The index contains files outside this source allowlist")
    if stage:
        subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "add",
                "--",
                *inventory,
                *sorted(removed_sources.intersection(tracked_sources)),
            ],
            check=True,
        )
    result = {
        "staged": stage,
        "files": inventory,
        "file_count": len(inventory),
        "bytes": sum(item["bytes"] for item in inventory.values()),
        "known_credentials_scan": "passed",
        "includes_final_case_documents": False,
        "includes_private_runs": False,
        "includes_development_dataset": True,
        "removed_source_paths": sorted(removed_sources),
    }
    write_json(report, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(stage=args.stage, report=args.report)
    print(json.dumps({key: value for key, value in result.items() if key != "files"}))
