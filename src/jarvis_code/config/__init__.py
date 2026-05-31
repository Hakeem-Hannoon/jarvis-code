"""Configuration: pydantic schema, TOML loader, env overrides, secret handling."""

from __future__ import annotations

from .errors import (
    ConfigError,
    ConfigNotFoundError,
    ConfigValidationError,
    MissingAPIKeyError,
)
from .loader import (
    default_config_path,
    env_var_for,
    find_config_path,
    load_config,
    require_provider,
    validate_config_file,
)
from .schema import (
    JarvisConfig,
    MicMode,
    ProviderConfig,
    SttEngineName,
    VoiceConfig,
    VoiceEngineName,
    VoiceInputConfig,
    VoiceOutputConfig,
)

__all__ = [
    "ConfigError",
    "ConfigNotFoundError",
    "ConfigValidationError",
    "MissingAPIKeyError",
    "default_config_path",
    "env_var_for",
    "find_config_path",
    "load_config",
    "require_provider",
    "validate_config_file",
    "JarvisConfig",
    "MicMode",
    "ProviderConfig",
    "SttEngineName",
    "VoiceConfig",
    "VoiceEngineName",
    "VoiceInputConfig",
    "VoiceOutputConfig",
]
