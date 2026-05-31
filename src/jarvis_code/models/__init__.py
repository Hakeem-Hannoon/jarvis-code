"""Model provider abstraction + per-provider clients."""

from __future__ import annotations

from .anthropic import AnthropicProvider
from .base import HTTPProvider, ModelProvider, ProviderError
from .factory import SUPPORTED_PROVIDERS, build_provider
from .gemini_style import GeminiStyleProvider
from .openai_style import OpenAIStyleProvider
from .types import Message, ModelResponse, Role, ToolCall, ToolSpec

__all__ = [
    "AnthropicProvider",
    "GeminiStyleProvider",
    "OpenAIStyleProvider",
    "HTTPProvider",
    "ModelProvider",
    "ProviderError",
    "build_provider",
    "SUPPORTED_PROVIDERS",
    "Message",
    "ModelResponse",
    "Role",
    "ToolCall",
    "ToolSpec",
]
