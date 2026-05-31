"""Config errors with the exact friendly messages the CLI promises."""

from __future__ import annotations


class ConfigError(Exception):
    """Base class for configuration problems."""


class ConfigNotFoundError(ConfigError):
    """No config file could be found."""

    def __init__(self) -> None:
        super().__init__(
            "No provider config found.\n"
            "Run: jarvis init\n"
            "Then edit: .jarvis/config.toml"
        )


class MissingAPIKeyError(ConfigError):
    """A provider was selected but has no API key (file or env)."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(
            f"No API key found for provider '{provider}'.\n"
            f"Add it to .jarvis/config.toml or set {provider.upper()}_API_KEY."
        )


class ConfigValidationError(ConfigError):
    """The config file exists but is malformed or fails schema validation."""
