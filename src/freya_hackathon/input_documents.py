"""Deterministic document inputs; unsupported or unreadable content fails explicitly."""

from __future__ import annotations

from pathlib import Path

from freya_hackathon.office_documents import extract_office_document

_BINARY_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods",
    ".zip", ".7z", ".png", ".jpg", ".jpeg", ".gif", ".webp",
}


def expand_briefs(paths: list[Path]) -> list[Path]:
    """Keep argument order, sort directory entries, and deduplicate exact file paths."""
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path.is_dir():
            entries = sorted(
                (item for item in path.rglob("*")
                 if not any(part.startswith(".") for part in item.relative_to(path).parts)),
                key=lambda item: item.relative_to(path).as_posix(),
            )
            if any(item.is_symlink() for item in entries):
                raise ValueError(
                    f"Brief directories must contain regular documents, not links: {path}"
                )
            files = [item for item in entries if item.is_file()]
            if not files:
                raise ValueError(f"Brief directory contains no visible documents: {path}")
        else:
            files = [path]
        for item in files:
            identity = item.resolve()
            if identity not in seen:
                seen.add(identity)
                result.append(item)
    if not result:
        raise ValueError("Supply at least one brief document")
    return result


def decode_document(path: Path, data: bytes) -> str:
    """Do not silently omit or misdecode an unsupported policy attachment."""
    if path.suffix.lower() in {".pdf", ".docx", ".xlsx", ".xlsm"}:
        return extract_office_document(path, data)
    binary = (path.suffix.lower() in _BINARY_SUFFIXES
              or data.startswith((b"%PDF-", b"PK\x03\x04", b"\xd0\xcf\x11\xe0"))
              or b"\x00" in data)
    if binary:
        raise ValueError(
            f"Unsupported binary document {path.name}; export its text as UTF-8 "
            "and supply it as a brief or --additional-brief. No document was skipped."
        )
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Document {path.name} is not UTF-8 text; convert it to UTF-8 "
            "before building. No document was skipped."
        ) from exc
