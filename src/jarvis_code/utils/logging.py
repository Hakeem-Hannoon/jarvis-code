"""Logging helpers + a process-wide secret redactor.

``get_logger`` is the single logging seam. The :class:`RedactingFilter` and the
module-level :func:`redact` function guarantee that any registered secret value
(API keys, resolved at config load time) is scrubbed from log records *and* from
any text we persist (e.g. session transcripts) — so keys can never reach logs,
transcripts, or the console regardless of who emits them.
"""

from __future__ import annotations

import io
import logging
import os
import re
from collections.abc import Mapping

_ROOT_NAME = "jarvis_code"

# Minimum length for a string to be treated as a redactable secret. Avoids
# accidentally redacting tiny/common substrings that merely collide with a key.
_MIN_SECRET_LEN = 6

_REDACTION = "***REDACTED***"
#: Public alias for the marker that replaces a redacted secret.
REDACTION_MARKER = _REDACTION

# Environment variables whose *name* looks like a credential. These are stripped
# from any subprocess environment (see :func:`scrub_secret_env`) so a shell or
# git command can never read a key back into the model's context — even a secret
# we never registered. PATH/HOME/etc. are untouched.
_SECRET_ENV_NAME = re.compile(
    r"(?:_API_KEY|_APIKEY|_ACCESS_KEY|_SECRET_KEY|_SECRET|_TOKEN|_PASSWORD|_PASSWD"
    r"|_CREDENTIALS?)$",
    re.IGNORECASE,
)


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


class _SecretRedactor:
    """Holds the set of secret values to scrub from any outgoing text."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def register(self, value: object) -> None:
        if isinstance(value, str) and len(value) >= _MIN_SECRET_LEN:
            self._secrets.add(value)

    def redact(self, text: str) -> str:
        if not text or not self._secrets:
            return text
        # Replace longer secrets first so a key that contains another is handled.
        for secret in sorted(self._secrets, key=len, reverse=True):
            if secret in text:
                text = text.replace(secret, _REDACTION)
        return text

    def scrub_env(self, env: Mapping[str, str]) -> dict[str, str]:
        """Copy ``env`` dropping credential-named vars and registered values."""
        out: dict[str, str] = {}
        for key, value in env.items():
            if _SECRET_ENV_NAME.search(key):
                continue
            if isinstance(value, str) and value in self._secrets:
                continue
            out[key] = value
        return out

    def clear(self) -> None:
        self._secrets.clear()


_redactor = _SecretRedactor()


def register_secret(value: object) -> None:
    """Register a secret value to be scrubbed everywhere from now on."""
    _redactor.register(value)


def redact(text: str) -> str:
    """Return ``text`` with every registered secret replaced by a marker."""
    return _redactor.redact(text)


def scrub_secret_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a copy of ``env`` (default ``os.environ``) safe for a subprocess.

    Removes (a) every variable whose *value* is a registered secret — precisely
    dropping the configured provider API keys however they happen to be named —
    and (b) variables whose *name* matches a credential-shaped pattern. Pass the
    result as ``subprocess.run(..., env=...)`` so a child process cannot read a
    key back into captured output (and thus into model context).
    """
    return _redactor.scrub_env(os.environ if env is None else env)


def clear_secrets() -> None:
    """Forget all registered secrets (used by tests)."""
    _redactor.clear()


class RedactingFilter(logging.Filter):
    """A logging filter that scrubs registered secrets from every record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            message = str(record.msg)
        record.msg = _redactor.redact(message)
        record.args = ()
        return True


_configured = False


def configure_logging(
    level: int = logging.WARNING,
    *,
    stream: io.TextIOBase | None = None,
    force: bool = False,
) -> logging.Logger:
    """Install a single redacting handler on the ``jarvis_code`` root logger.

    Idempotent unless ``force=True``. Secrets are scrubbed at the handler via
    :class:`RedactingFilter`, so nothing emitted through jarvis logging can leak
    a registered key.
    """
    global _configured
    logger = logging.getLogger(_ROOT_NAME)
    if _configured and not force:
        return logger

    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())

    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    # Don't propagate to the root logger: that would bypass our redacting
    # handler and could echo a record (pre-redaction) through a foreign handler.
    logger.propagate = False
    _configured = True
    return logger
