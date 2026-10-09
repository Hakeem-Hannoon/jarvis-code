"""Save the current provider / model / effort as the default, in place.

``/save`` should change three values in the user's config and nothing else:
their comments, layout and API keys stay exactly as written. So this edits the
TOML *text* line by line (re-serializing through a TOML library would drop every
comment), then proves the result before it touches the disk:

* the new text must parse and validate as a ``JarvisConfig``;
* it must actually say what we meant to save;
* every other value must be unchanged — in particular no key was altered.

Only then is it written, atomically, with the file's permissions preserved.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from pathlib import Path

from pydantic import ValidationError

from jarvis_code.config import ConfigValidationError, Effort, JarvisConfig

_SECTION = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


def _assignment(key: str) -> re.Pattern[str]:
    # key = <value>   # optional trailing comment
    return re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)(\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^#\n]*?)(\s*(#.*)?)$")


def _set_in_section(lines: list[str], section: str | None, key: str, value: str) -> None:
    """Set ``key = "value"`` inside ``[section]`` (None = top level), in place."""
    quoted = json.dumps(value)  # a JSON string is a valid TOML basic string
    pattern = _assignment(key)

    start, end = 0, len(lines)
    if section is None:
        for i, line in enumerate(lines):
            if _SECTION.match(line):
                end = i
                break
    else:
        header = next(
            (i for i, line in enumerate(lines)
             if (m := _SECTION.match(line)) and m.group(1) == section),
            None,
        )
        if header is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines += [f"[{section}]", f"{key} = {quoted}"]
            return
        start = header + 1
        for i in range(start, len(lines)):
            if _SECTION.match(lines[i]):
                end = i
                break

    for i in range(start, end):
        match = pattern.match(lines[i])
        if match:
            lines[i] = f"{match.group(1)}{quoted}{match.group(3)}"
            return
    # Not present yet: add it at the top of its section.
    lines.insert(start, f"{key} = {quoted}")


def _without(config: dict, provider: str) -> dict:
    """``config`` minus the three values /save is allowed to change."""
    clone = json.loads(json.dumps(config, default=str))
    clone.pop("default_provider", None)
    clone.get("providers", {}).get(provider, {}).pop("model", None)
    clone.get("agent", {}).pop("effort", None)
    if not clone.get("agent"):
        clone.pop("agent", None)
    return clone


def updated_config_text(text: str, *, provider: str, model: str, effort: Effort) -> str:
    """Return ``text`` with the defaults changed; raises if that can't be done safely."""
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigValidationError(f"The config file is not valid TOML: {exc}") from exc
    if provider not in (before.get("providers") or {}):
        raise ConfigValidationError(
            f"Provider '{provider}' has no [providers.{provider}] section in this file."
        )

    lines = text.splitlines()
    _set_in_section(lines, None, "default_provider", provider)
    _set_in_section(lines, f"providers.{provider}", "model", model)
    _set_in_section(lines, "agent", "effort", effort.value)
    new_text = "\n".join(lines) + ("\n" if text.endswith("\n") or not text else "")

    try:
        after = tomllib.loads(new_text)
        validated = JarvisConfig.model_validate(after)
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ConfigValidationError(f"Refusing to save: the edit would break the config ({exc}).") from exc
    saved = (
        validated.default_provider == provider
        and validated.providers[provider].model == model
        and validated.agent.effort == effort
    )
    if not saved or _without(before, provider) != _without(after, provider):
        # The text edit landed somewhere unexpected (an unusual layout). Don't guess.
        raise ConfigValidationError(
            "Refusing to save: couldn't update this config's layout safely. "
            "Edit default_provider / model / effort in the file by hand."
        )
    return new_text


def save_defaults(path: Path, *, provider: str, model: str, effort: Effort) -> None:
    """Rewrite ``path`` so these become the defaults. Atomic; permissions kept."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigValidationError(f"Could not read {path}: {exc}") from exc
    new_text = updated_config_text(text, provider=provider, model=model, effort=effort)
    if new_text == text:
        return
    mode = path.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        os.chmod(tmp, mode)  # the file holds API keys: never widen its permissions
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise ConfigValidationError(f"Could not write {path}: {exc}") from exc
