"""The local source archive must exclude credentials and preserve exact inputs."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "package_builder", Path(__file__).resolve().parents[1] / "diagnostics/package_builder.py"
)
assert SPEC and SPEC.loader
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


@pytest.fixture
def source_tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "source"
    files = (
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "README.md",
        "diagnostics/finalize_submission.py",
        "diagnostics/package_builder.py",
        "vendor/freya-v3/README.md",
        "vendor/freya-v3/VERSION",
        "vendor/freya-v3/freya.py",
        "vendor/freya-v3/tools/run_cases.py",
        "vendor/freya-v3/tools/score.py",
        "vendor/freya-v3/docs/workflow-format.md",
        "src/freya_hackathon/builder.py",
        "benchmarks/freya-execution.yaml",
        "benchmarks/development-references-v1/single-product.json",
    )
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {name}\n")
    guidance = tmp_path / "selected-guidance.txt"
    guidance.write_text("Use supplied policy and preserve grounded consent.\n")
    return root, guidance


def test_exact_selected_guidance_and_allowlist(source_tree, tmp_path: Path) -> None:
    root, guidance = source_tree
    secret = "fixture-secret-never-archive"
    for name in (".env", ".freya-team-key", "vendor/freya-v3/env.sh", "runs/private.json"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret)
    output = tmp_path / "builder.zip"
    report = PACKAGE.build_bundle(root, guidance, output)
    assert report["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    with zipfile.ZipFile(output) as archive:
        assert archive.read("benchmarks/builder-guidance.txt") == guidance.read_bytes()
        assert (
            archive.read("vendor/freya-v3/tools/run_cases.py")
            == (root / "vendor/freya-v3/tools/run_cases.py").read_bytes()
        )
        assert (
            archive.read("vendor/freya-v3/tools/score.py")
            == (root / "vendor/freya-v3/tools/score.py").read_bytes()
        )
        assert all(secret.encode() not in archive.read(name) for name in archive.namelist())
        manifest = json.loads(archive.read("bundle-manifest.json"))
        assert manifest["official_submission"] is False
        for name, evidence in manifest["files"].items():
            data = archive.read(name)
            assert hashlib.sha256(data).hexdigest() == evidence["sha256"]
            assert len(data) == evidence["bytes"]


@pytest.mark.parametrize("parent_link", [False, True])
def test_rejects_source_symlink_escape(source_tree, tmp_path: Path, parent_link: bool) -> None:
    root, guidance = source_tree
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "unexpected.py").write_text("private fixture")
    if parent_link:
        source = root / "src/freya_hackathon"
        (source / "builder.py").unlink()
        source.rmdir()
        source.symlink_to(outside, target_is_directory=True)
    else:
        (root / "src/freya_hackathon/unexpected.py").symlink_to(outside / "unexpected.py")
    output = tmp_path / "builder.zip"
    with pytest.raises(ValueError, match="Unsafe"):
        PACKAGE.build_bundle(root, guidance, output)
    assert not output.exists()


def test_existing_archive_is_never_overwritten(source_tree, tmp_path: Path) -> None:
    root, guidance = source_tree
    output = tmp_path / "builder.zip"
    output.write_bytes(b"existing evidence")
    with pytest.raises(ValueError, match="already exists"):
        PACKAGE.build_bundle(root, guidance, output)
    assert output.read_bytes() == b"existing evidence"


def test_selected_reference_version_is_pinned_in_archive_and_runbook(
    source_tree, tmp_path: Path
) -> None:
    root, guidance = source_tree
    selected = root / "benchmarks/development-references-v6/single-product.json"
    selected.parent.mkdir()
    selected.write_text('{"selected": true}\n')
    output = tmp_path / "selected.zip"
    PACKAGE.build_bundle(root, guidance, output, reference_version="v6")
    with zipfile.ZipFile(output) as archive:
        assert archive.read(str(selected.relative_to(root))) == selected.read_bytes()
        assert not any("development-references-v1/" in name for name in archive.namelist())
        assert b"development-references-v6/" in archive.read("RUNBOOK.txt")
        assert b"development-references-v1/" not in archive.read("RUNBOOK.txt")
        assert json.loads(archive.read("bundle-manifest.json"))["reference_version"] == "v6"


@pytest.mark.parametrize("version", ["../../.env", "v0", "v99"])
def test_invalid_or_missing_reference_version_cannot_create_bundle(
    source_tree, tmp_path: Path, version: str
) -> None:
    root, guidance = source_tree
    output = tmp_path / "invalid.zip"
    with pytest.raises(ValueError, match="reference"):
        PACKAGE.build_bundle(root, guidance, output, reference_version=version)
    assert not output.exists()
