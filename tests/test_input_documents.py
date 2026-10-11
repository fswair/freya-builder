from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from freya_hackathon.builder import KIT, load_inputs
from freya_hackathon.input_documents import expand_briefs


def test_directory_sources_are_complete_ordered_deduplicated_and_hashed(tmp_path: Path):
    directory = tmp_path / "brief-material"
    directory.mkdir()
    nested = directory / "policies"
    nested.mkdir()
    # Deliberately create files out of their lexical order.
    amendment = nested / "20-amendment.txt"
    amendment.write_text("The deadline is Friday.\n", encoding="utf-8")
    first = directory / "10-brief.md"
    first.write_bytes(b"\xef\xbb\xbfCheck identity.\n")
    (directory / ".DS_Store").write_bytes(b"\x00metadata")
    extra = tmp_path / "extra.eml"
    extra.write_text("Subject: New policy\n\nKeep the previous identity rule.", encoding="utf-8")
    inputs = load_inputs([directory, first, extra], KIT / "tools/openapi.json")
    assert list(inputs.sources) == ["brief-1", "brief-2", "brief-3", "tool-api"]
    assert inputs.sources["brief-1"] == "Check identity.\n"
    assert inputs.sources["brief-2"] == amendment.read_text()
    assert inputs.sources["brief-3"] == extra.read_text()
    assert [r["path"] for r in inputs.manifest[:3]] == [str(first), str(amendment), str(extra)]
    assert inputs.manifest[0]["sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()
    again = load_inputs([directory, first, extra], KIT / "tools/openapi.json")
    assert inputs == again


@pytest.mark.parametrize(
    ("filename", "content", "error"),
    [
        ("policy.pdf", b"%PDF-1.7\nASCII PDF data", "Cannot extract policy.pdf"),
        ("attachment.txt", b"PK\x03\x04zip data", "Unsupported binary"),
        ("policy.docx", b"PK\x03\x04document archive", "Cannot extract policy.docx"),
        ("policy.txt", b"A\x00B", "Unsupported binary"),
        ("email.eml", b"Amount: \xff", "not UTF-8 text"),
    ],
)
def test_unsupported_attachment_is_not_silently_dropped(tmp_path, filename, content, error):
    (tmp_path / "01-readable.md").write_text("Check identity.")
    attachment = tmp_path / filename
    attachment.write_bytes(content)
    for supplied in (tmp_path, attachment):
        with pytest.raises(ValueError, match=error):
            load_inputs([supplied], KIT / "tools/openapi.json")


def test_empty_directory_and_no_documents_fail_before_generation(tmp_path):
    with pytest.raises(ValueError, match="no visible documents"):
        expand_briefs([tmp_path])
    with pytest.raises(ValueError, match="at least one brief"):
        expand_briefs([])


def test_directory_link_is_not_followed_into_another_input_tree(tmp_path):
    material = tmp_path / "material"
    material.mkdir()
    other = tmp_path / "separate.txt"
    other.write_text("This document was not supplied in the directory.")
    (material / "linked.txt").symlink_to(other)
    with pytest.raises(ValueError, match="not links"):
        expand_briefs([material])
