"""The ModelProvider protocol and a shared HTTP base.

A provider turns ``(messages, tools)`` into a normalized :class:`ModelResponse`.
The API key is held privately on the provider and used only to build request
headers — it is never written into messages or any object the model can see.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from jarvis_code.utils.logging import get_logger, redact

from .types import Message, ModelResponse, ToolSpec

_log = get_logger("models")


class ProviderError(Exception):
    """A provider call failed (transport or API error). Never carries a key."""


@runtime_checkable
class ModelProvider(Protocol):
    name: str
    model: str

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
    ) -> ModelResponse: ...


class HTTPProvider:
    """Shared HTTP plumbing. Subclasses implement request/response mapping.

    The key is stored as ``self._api_key`` and only ever placed in headers via
    :meth:`_build_request`. ``__repr__`` never reveals it.
    """

    name: str = "http"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        endpoint: str | None = None,
        api_version: str | None = None,
        project: str | None = None,
        location: str | None = None,
        endpoint_style: str | None = None,
        http_client: httpx.Client | None = None,
        timeout: float = 300.0,
        max_tokens: int = 4096,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self.endpoint = endpoint
        self.api_version = api_version
        self.project = project
        self.location = location
        self.endpoint_style = endpoint_style
        self.max_tokens = max_tokens
        # Connect should fail fast, but the model may "think" for minutes — give
        # the read/write/pool the full timeout so long generations don't abort.
        self._client = http_client or httpx.Client(
            timeout=httpx.Timeout(timeout, connect=15.0)
        )

    def __repr__(self) -> str:  # never leak the key
        return f"<{type(self).__name__} provider={self.name!r} model={self.model!r}>"

    # -- subclass hooks ----------------------------------------------------

    def _build_request(
        self, messages: list[Message], tools: list[ToolSpec] | None
    ) -> tuple[str, str, dict, dict]:
        """Return ``(method, url, headers, json_body)``. Key goes in headers."""
        raise NotImplementedError

    def _parse_response(self, data: dict) -> ModelResponse:
        raise NotImplementedError

    # -- public ------------------------------------------------------------

    def complete(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> ModelResponse:
        method, url, headers, body = self._build_request(messages, tools)
        try:
            response = self._client.request(method, url, headers=headers, json=body)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # redact() scrubs any registered key just in case it appears in text.
            msg = redact(f"{self.name} API error {exc.response.status_code}: {exc.response.text}")
            _log.warning("%s", msg)
            raise ProviderError(msg) from exc
        except httpx.HTTPError as exc:
            msg = redact(f"{self.name} transport error: {exc}")
            raise ProviderError(msg) from exc
        return self._parse_response(response.json())
