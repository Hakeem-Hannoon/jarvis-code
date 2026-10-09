"""jarvis's own config files are off-limits to the agent's file tools.

Two reasons, both about API keys:

* **Reading.** Keys are kept out of model context by redacting the values the
  loader registered. A *second* config file in the project (say ``.jarvis.toml``
  next to the ``.jarvis/config.toml`` that was actually loaded) holds values
  nobody registered, so reading it would put raw keys in front of the model.
* **Writing.** A config the agent can create or edit is a config a prompt
  injection can edit: point ``endpoint`` at another host and the next run sends
  the user's key there.

The agent never has a legitimate reason to touch these, so the deterministic
tool layer refuses — in every mode, ``dangerous`` included. (The shell is the
documented exception: it needs OS-level sandboxing; see docs/security.md.)
"""

from __future__ import annotations

import os
from pathlib import Path

from jarvis_code.config import PROJECT_CONFIG_CANDIDATES
from jarvis_code.sandbox import Sandbox, SandboxViolation

REFUSAL = (
    "is jarvis's own configuration (it can hold API keys) and is off-limits to the agent."
)


def _protected_paths(sandbox: Sandbox) -> list[Path]:
    out = []
    for candidate in PROJECT_CONFIG_CANDIDATES:
        path = sandbox.root / candidate
        out.append(path)
        try:
            out.append(path.resolve())  # the same file reached through a symlink
        except OSError:  # pragma: no cover - unreadable parent
            pass
    return out


def protected_reason(
    sandbox: Sandbox,
    path: str | os.PathLike[str],
    *,
    contains: bool = False,
) -> str | None:
    """Why ``path`` may not be touched, or None if it is fine.

    With ``contains=True`` a directory that *holds* a config file is protected
    too (deleting or moving ``.jarvis/`` would take the config with it).
    """
    try:
        candidates = {
            sandbox.safe_path(path),
            sandbox.safe_path(path, follow_symlinks=False),
        }
    except SandboxViolation:
        return None  # out of root: the sandbox itself rejects it with its own error
    for protected in _protected_paths(sandbox):
        for candidate in candidates:
            if candidate == protected or (contains and candidate in protected.parents):
                return f"{os.fspath(path)!r} {REFUSAL}"
    return None


def is_protected_file(sandbox: Sandbox, file: Path) -> bool:
    """True if a walked file is a config file (search tools skip these)."""
    protected = _protected_paths(sandbox)
    try:
        return file in protected or file.resolve() in protected
    except OSError:  # pragma: no cover
        return False
