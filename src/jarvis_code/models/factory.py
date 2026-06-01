"""Provider selection + construction from config and CLI flags.

The agent loop only ever calls :func:`build_provider`; swapping providers never
touches the loop. Keys are pulled from the secrets layer here and handed to the
provider for header use only.
"""

from __future__ import annotations

import httpx

from jarvis_code.config import JarvisConfig, require_provider

from .anthropic import AnthropicProvider
from .base import ModelProvider
from .gemini_style import GeminiStyleProvider
from .openai_style import OpenAIStyleProvider

SUPPORTED_PROVIDERS = ("anthropic", "openai", "deepseek", "gemini", "vertex", "azure")


def build_provider(
    config: JarvisConfig,
    *,
    provider: str | None = None,
    model: str | None = None,
    http_client: httpx.Client | None = None,
    max_tokens: int | None = None,
) -> ModelProvider:
    """Construct the selected provider.

    Selection precedence: ``provider`` flag, else ``config.default_provider``.
    Model precedence: ``model`` flag, else the provider's configured model.
    ``max_tokens`` (from the effort profile) bounds the response when given.
    Raises ``MissingAPIKeyError`` (from the config layer) if the key is absent.
    """
    name, pconf = require_provider(config, provider)
    model_name = model or pconf.model
    api_key = pconf.api_key.get_secret_value()  # type: ignore[union-attr]

    common = {
        "model": model_name,
        "api_key": api_key,
        "endpoint": pconf.endpoint,
        "api_version": pconf.api_version,
        "project": pconf.project,
        "location": pconf.location,
        "endpoint_style": pconf.endpoint_style,
        "http_client": http_client,
    }
    if max_tokens is not None:
        common["max_tokens"] = max_tokens

    if name == "anthropic":
        return AnthropicProvider(**common)
    if name in ("openai", "deepseek", "azure"):
        return OpenAIStyleProvider(variant=name, **common)
    if name in ("gemini", "vertex"):
        return GeminiStyleProvider(variant=name, **common)

    raise ValueError(
        f"Unsupported provider '{name}'. Supported: {', '.join(SUPPORTED_PROVIDERS)}"
    )
