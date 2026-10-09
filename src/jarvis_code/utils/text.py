"""Make untrusted text safe to print to a terminal.

Model output, tool arguments and file contents are untrusted. Printed raw, an
embedded escape sequence can move the cursor, clear lines or recolor text — e.g.
hide the dangerous half of a shell command in the approval prompt, or draw a
fake "approved" line. Everything model-controlled is passed through
:func:`sanitize_terminal` before it is echoed, so control characters show up as
visible ``\\x1b``-style escapes instead of being executed by the terminal.
"""

from __future__ import annotations

import re

# C0/C1 control characters except newline and tab, plus the Unicode bidi
# overrides/isolates that can visually reorder text ("Trojan Source").
_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")


def _visible(match: re.Match[str]) -> str:
    code = ord(match.group())
    return f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}"


def sanitize_terminal(text: str) -> str:
    """Return ``text`` with terminal control characters rendered visibly."""
    if not text:
        return text
    return _UNSAFE.sub(_visible, text)
