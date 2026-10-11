"""Reduce sandbox source bytes without changing its Python syntax tree."""

from __future__ import annotations

import ast
import io
import tokenize


def compact_code(code: str) -> str:
    """Remove layout only; keep every literal, name and operation unchanged.

    The AST equality check is mandatory, including for unusual multiline strings
    and f-strings. If any proposed layout change is unsafe, retain the original.
    This saves input budget; it cannot bound the platform's accompanying data.
    """
    original = ast.parse(code)
    tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
    lines = code.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits: list[tuple[int, int, str]] = []
    layout = {
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.NEWLINE,
        tokenize.NL,
        tokenize.ENDMARKER,
        tokenize.COMMENT,
    }
    words = {tokenize.NAME, tokenize.NUMBER}
    depth = 0
    seen_rows: set[int] = set()
    for token in tokens:
        if token.type == tokenize.INDENT:
            depth += 1
        elif token.type == tokenize.DEDENT:
            depth -= 1
        elif token.type not in layout:
            row, col = token.start
            if row not in seen_rows and row <= len(lines):
                prefix = lines[row - 1][:col]
                if prefix and prefix.isspace() and len(prefix) > depth:
                    edits.append((offsets[row - 1], offsets[row - 1] + col, " " * depth))
            seen_rows.add(row)
    for left, right in zip(tokens, tokens[1:], strict=False):
        if left.end[0] != right.start[0] or left.type in layout or right.type in layout:
            continue
        start = offsets[left.end[0] - 1] + left.end[1]
        end = offsets[right.start[0] - 1] + right.start[1]
        if not code[start:end] or not code[start:end].isspace():
            continue
        if left.type in words and right.type in words:
            continue
        if {left.type, right.type} == {tokenize.STRING, tokenize.NAME}:
            continue
        if (left.type == tokenize.NUMBER and right.string == ".") or (
            left.string == "." and right.type == tokenize.NUMBER
        ):
            continue
        if left.type == right.type == tokenize.OP:
            continue
        edits.append((start, end, ""))
    candidate = code
    for start, end, replacement in sorted(edits, reverse=True):
        candidate = candidate[:start] + replacement + candidate[end:]
    try:
        same = ast.dump(original) == ast.dump(ast.parse(candidate))
    except (SyntaxError, ValueError):
        return code
    return candidate if same and len(candidate.encode()) < len(code.encode()) else code
