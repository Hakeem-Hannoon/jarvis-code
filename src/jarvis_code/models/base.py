"""The ModelProvider protocol and a shared HTTP base.

A provider turns ``(messages, tools)`` into a normalized :class:`ModelResponse`.
The API key is held privately on the provider and used only to build request
headers — it is never written into messages or any object the model can see.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

import httpx

from jarvis_code.utils.logging import get_logger, redact

from .types import Message, ModelResponse, ToolSpec

_log = get_logger("models")


class ProviderError(Exception):
    """A provider call failed (transport or API error). Never carries a key."""


# Transient failures worth retrying: rate limits, overload, gateway hiccups.
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
_MAX_ATTEMPTS = 4
_MAX_RETRY_AFTER = 30.0

# One-line hints for the statuses a user can actually act on.
_STATUS_HINTS = {
    401: "the API key was rejected — check it in your config or environment",
    403: "this key is not allowed to use that model or endpoint",
    404: "model or endpoint not found — check the model name (--model) and endpoint",
    413: "the request is too large — try /compress to shrink the conversation",
    429: "rate limited — wait a moment, or check your plan's limits",
    529: "the provider is overloaded — try again shortly",
}


def _error_detail(response: httpx.Response) -> str:
    """The human message inside an API error body (falls back to the raw text)."""
    try:
        data = response.json()
    except ValueError:
        data = None
    for candidate in (data, data.get("error") if isinstance(data, dict) else None):
        if isinstance(candidate, dict) and isinstance(candidate.get("message"), str):
            return candidate["message"]
    if isinstance(data, dict) and isinstance(data.get("error"), str):
        return data["error"]
    return response.text.strip()[:500]


def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
    """Seconds to wait before retry ``attempt`` (1-based); honors Retry-After."""
    if response is not None:
        try:
            return max(0.0, min(_MAX_RETRY_AFTER, float(response.headers.get("retry-after", ""))))
        except ValueError:
            pass
    return float(min(8, 2 ** (attempt - 1)))  # 1s, 2s, 4s


def as_arguments(value: object) -> dict:
    """Tool-call arguments as a dict; anything else (list, string, null) is ``{}``.

    Providers occasionally return non-object "arguments". The runtime then
    reports the missing required argument back to the model instead of crashing.
    """
    return value if isinstance(value, dict) else {}


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
        response = self._send(method, url, headers, body)
        try:
            data = response.json()
        except ValueError as exc:  # a 200 with an HTML/empty body (proxy, gateway…)
            snippet = redact(response.text[:200])
            raise ProviderError(
                f"{self.name} returned a non-JSON response: {snippet!r}"
            ) from exc
        if not isinstance(data, dict):
            raise ProviderError(f"{self.name} returned an unexpected response shape.")
        return self._parse_response(data)

    # Injectable so tests don't actually wait out a backoff.
    _sleep = staticmethod(time.sleep)

    def _send(self, method: str, url: str, headers: dict, body: dict) -> httpx.Response:
        """Send the request, retrying transient failures with backoff.

        One overloaded/rate-limited response or a dropped connection should not
        end the user's turn. A read *timeout* is not retried: the request already
        waited the full (minutes-long) timeout once.
        """
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            last = attempt == _MAX_ATTEMPTS
            try:
                response = self._client.request(method, url, headers=headers, json=body)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError,
                    httpx.ReadError, httpx.WriteError) as exc:
                if last:
                    raise ProviderError(
                        redact(f"{self.name} connection failed after {attempt} attempts: {exc}")
                    ) from exc
                _log.info("%s connection error (%s); retrying", self.name, type(exc).__name__)
                self._sleep(_retry_delay(attempt, None))
                continue
            except httpx.HTTPError as exc:
                raise ProviderError(redact(f"{self.name} transport error: {exc}")) from exc

            if response.status_code < 400:
                return response
            if response.status_code in _RETRY_STATUS and not last:
                _log.info("%s returned %s; retrying", self.name, response.status_code)
                self._sleep(_retry_delay(attempt, response))
                continue
            # redact() scrubs any registered key just in case it appears in text.
            status = response.status_code
            msg = f"{self.name} API error {status}: {_error_detail(response)}"
            hint = _STATUS_HINTS.get(status)
            if hint:
                msg += f"\n  hint: {hint}"
            msg = redact(msg)
            _log.debug("%s", msg)
            raise ProviderError(msg)
        raise ProviderError(f"{self.name} request failed.")  # pragma: no cover
