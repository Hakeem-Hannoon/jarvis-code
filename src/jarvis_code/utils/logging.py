"""Logging helpers.

Phase 1 provides a thin ``get_logger`` seam so every module logs through one
place. Phase 2 installs a secret-redacting filter here so API keys can never
reach any log record, regardless of which logger emitted it.
"""

from __future__ import annotations

import logging

_ROOT_NAME = "jarvis_code"


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger under the ``jarvis_code`` root.

    ``name`` may be a bare module name (e.g. ``"sandbox"``) or a dotted path;
    it is placed under the ``jarvis_code`` root logger either way.
    """
    if name == _ROOT_NAME or name.startswith(_ROOT_NAME + "."):
        full = name
    else:
        full = f"{_ROOT_NAME}.{name}"
    return logging.getLogger(full)
