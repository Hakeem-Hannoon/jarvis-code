"""Load + validate config from TOML, apply env overrides, register secrets.

Load order (later wins): file values -> environment variables. Each provider's
key comes from ``<PROVIDER>_API_KEY`` if set, otherwise the file. Resolved keys
are registered with the redactor so they can never appear in logs or transcripts.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path

from pydantic import SecretStr, ValidationError

from jarvis_code.utils.logging import get_logger, register_secret

from .errors import ConfigNotFoundError, ConfigValidationError, MissingAPIKeyError
from .schema import JarvisConfig, ProviderConfig

_log = get_logger("config")

# Where `jarvis init` writes the real config, plus a convenience fallback.
_CONFIG_CANDIDATES = (
    Path(".jarvis") / "config.toml",
    Path(".jarvis.toml"),
)


def env_var_for(provider: str) -> str:
    """The environment variable that overrides a provider's key."""
    return f"{provider.upper()}_API_KEY"


def global_config_path() -> Path:
    """The primary global (per-user) config path. Honors XDG_CONFIG_HOME."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "jarvis" / "config.toml"


def _global_candidates() -> list[Path]:
    return [global_config_path(), Path.home() / ".jarvis" / "config.toml"]


def find_config_path(base_dir: str | os.PathLike[str] | None = None) -> Path | None:
    """Find the config file.

    Project-local first (``<base>/.jarvis/config.toml`` or ``.jarvis.toml``) so a
    project can override, then the per-user global config — so you can configure
    once and run ``jarvis`` in any directory.
    """
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    for candidate in _CONFIG_CANDIDATES:
        path = base / candidate
        if path.is_file():
            return path
    for path in _global_candidates():
        if path.is_file():
            return path
    return None


def default_config_path(base_dir: str | os.PathLike[str] | None = None) -> Path:
    """The canonical project-local config path (whether or not it exists yet)."""
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    return base / _CONFIG_CANDIDATES[0]


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigValidationError(f"Could not parse TOML at {path}: {exc}") from exc
    except OSError as exc:  # pragma: no cover - unreadable file
        raise ConfigValidationError(f"Could not read config at {path}: {exc}") from exc


def _apply_env_overrides(config: JarvisConfig, env: Mapping[str, str]) -> None:
    """Env vars override file keys, in place, and register every key seen.

    Both the file-provided key and any env-override key are registered with the
    redactor (defense in depth) so neither can ever surface in logs/transcripts,
    even though only the override is actually used.
    """
    for name, provider in config.providers.items():
        # Register the file-provided key first (it lives on disk and is real).
        if provider.has_key():
            register_secret(provider.api_key.get_secret_value())  # type: ignore[union-attr]
        value = env.get(env_var_for(name))
        if value:
            provider.api_key = SecretStr(value)
            register_secret(value)


def load_config(
    path: str | os.PathLike[str] | None = None,
    *,
    base_dir: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> JarvisConfig:
    """Load and validate config.

    Raises :class:`ConfigNotFoundError` if no file is found, or
    :class:`ConfigValidationError` if the file is malformed/invalid.
    """
    env = os.environ if env is None else env

    if path is not None:
        config_path = Path(path)
        if not config_path.is_file():
            raise ConfigNotFoundError()
    else:
        found = find_config_path(base_dir)
        if found is None:
            raise ConfigNotFoundError()
        config_path = found

    raw = _read_toml(config_path)
    try:
        config = JarvisConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigValidationError(
            f"Config at {config_path} is invalid:\n{exc}"
        ) from exc

    _apply_env_overrides(config, env)
    _log.debug("loaded config from %s", config_path)
    return config


def require_provider(
    config: JarvisConfig,
    provider: str | None = None,
) -> tuple[str, ProviderConfig]:
    """Return ``(name, provider_config)`` for the selected provider.

    Uses ``provider`` if given, else ``config.default_provider``. Raises
    :class:`MissingAPIKeyError` if the provider has no key, or
    :class:`ConfigValidationError` if the provider is not configured.
    """
    name = provider or config.default_provider
    if name not in config.providers:
        raise ConfigValidationError(
            f"Provider '{name}' is not configured under [providers]. "
            f"Known: {sorted(config.providers)}"
        )
    provider_config = config.providers[name]
    if not provider_config.has_key():
        raise MissingAPIKeyError(name)
    return name, provider_config


def validate_config_file(
    path: str | os.PathLike[str] | None = None,
    *,
    base_dir: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Return a list of human-readable problems; empty means valid.

    Backs ``jarvis config validate``. Never raises for ordinary problems and
    never includes any key material in its messages.
    """
    problems: list[str] = []
    try:
        config = load_config(path, base_dir=base_dir, env=env)
    except ConfigNotFoundError as exc:
        return [str(exc)]
    except ConfigValidationError as exc:
        return [str(exc)]

    # Warn about providers with no usable key (not fatal — they're just unusable).
    for name, provider in config.providers.items():
        if not provider.has_key():
            problems.append(
                f"Provider '{name}' has no API key "
                f"(set it in config or export {env_var_for(name)})."
            )
    return problems
