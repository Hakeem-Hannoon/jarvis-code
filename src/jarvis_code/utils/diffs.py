"""Small diff/summary helpers for change previews shown before edits."""

from __future__ import annotations

import difflib


def unified_diff(old: str, new: str, path: str = "file", *, context: int = 3) -> str:
    """Return a unified diff between two text blobs (empty if identical)."""
    if old == new:
        return ""
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=context,
    )
    return "".join(diff)


def change_summary(old: str, new: str) -> str:
    """A one-line +adds/-dels summary of a text change."""
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    added = removed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return f"+{added} -{removed} lines"
