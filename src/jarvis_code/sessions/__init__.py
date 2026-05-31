"""Append-only JSONL session transcript persistence."""

from __future__ import annotations

from .writer import SessionWriter, new_session_id

__all__ = ["SessionWriter", "new_session_id"]
