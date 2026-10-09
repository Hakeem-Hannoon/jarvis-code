"""OpenAI-compatible chat/completions provider (OpenAI, DeepSeek, Azure Foundry).

These three share the chat/completions request/response shape and differ only in
base URL and auth header, so one class with a ``variant`` covers all three.
"""

from __future__ import annotations

import json
import re

from .base import HTTPProvider, as_arguments, new_call_id
from .types import Message, ModelResponse, Role, ToolCall, ToolSpec

_NOT_CHAT = re.compile(
    r"embed|whisper|tts|dall-e|image|audio|realtime|moderation|transcribe|sora|"
    r"babbage|davinci|search|similarity|computer-use",
    re.I,
)

_DEFAULT_BASE = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
}


def _messages_to_openai(messages: list[Message]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m.role == Role.TOOL:
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == Role.ASSISTANT and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": m.role.value, "content": m.content})
    return out


def _tools_to_openai(tools: list[ToolSpec] | None) -> list[dict] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in tools
    ]


class OpenAIStyleProvider(HTTPProvider):
    def __init__(self, *, variant: str, **kwargs) -> None:
        self.name = variant
        self._variant = variant
        super().__init__(**kwargs)

    def _is_foundry(self) -> bool:
        """True for Azure AI Foundry's model-inference endpoint shape.

        Explicit ``endpoint_style`` wins; otherwise detect by host. Foundry uses
        ``/models/chat/completions`` with the model in the body, whereas classic
        Azure OpenAI uses ``/openai/deployments/<deployment>/chat/completions``.
        """
        if self.endpoint_style in ("foundry", "azure_foundry"):
            return True
        if self.endpoint_style in ("azure_openai", "openai"):
            return False
        return "services.ai.azure.com" in (self.endpoint or "").lower()

    def _is_openai_v1(self, base: str) -> bool:
        """True for the OpenAI-compatible v1 surface (endpoint ends in /openai/v1)."""
        if self.endpoint_style in ("openai_v1", "v1"):
            return True
        return "/openai/v1" in base.lower()

    def _url_and_headers(self) -> tuple[str, dict]:
        if self._variant == "azure":
            base = (self.endpoint or "").rstrip("/")
            json_ct = {"content-type": "application/json"}
            # Case A: OpenAI-compatible v1 endpoint (Azure AI Foundry). The base
            # already includes /openai/v1; just append chat/completions. Model is
            # in the body, auth is Bearer, and there is no api-version param.
            if self._is_openai_v1(base):
                return f"{base}/chat/completions", {
                    "Authorization": f"Bearer {self._api_key}", **json_ct
                }
            # Case B: Foundry model-inference endpoint (model in body, api-key).
            if self._is_foundry():
                version = self.api_version or "2024-05-01-preview"
                return f"{base}/models/chat/completions?api-version={version}", {
                    "api-key": self._api_key, **json_ct
                }
            # Case C: classic Azure OpenAI (deployment in the URL path).
            version = self.api_version or "2024-06-01"
            url = f"{base}/openai/deployments/{self.model}/chat/completions?api-version={version}"
            return url, {"api-key": self._api_key, **json_ct}
        base = (self.endpoint or _DEFAULT_BASE[self._variant]).rstrip("/")
        return (
            f"{base}/chat/completions",
            {"Authorization": f"Bearer {self._api_key}", "content-type": "application/json"},
        )

    def _models_request(self):
        if self._variant == "azure":
            base = (self.endpoint or "").rstrip("/")
            # Only the OpenAI-compatible v1 surface lists models; classic
            # deployments and the Foundry inference route cannot be enumerated
            # with a data-plane key.
            if base and self._is_openai_v1(base):
                return f"{base}/models", {"Authorization": f"Bearer {self._api_key}"}
            return None
        base = (self.endpoint or _DEFAULT_BASE[self._variant]).rstrip("/")
        return f"{base}/models", {"Authorization": f"Bearer {self._api_key}"}

    def _parse_models(self, data: dict) -> list[str]:
        # The listing mixes in embedding / audio / image models that cannot chat.
        return [m for m in super()._parse_models(data) if not _NOT_CHAT.search(m)]

    def _max_tokens_field(self) -> str:
        """The name of the response-budget parameter for this endpoint.

        OpenAI's own API deprecated ``max_tokens`` for ``max_completion_tokens``;
        its reasoning models (o-series, GPT-5…) reject ``max_tokens`` outright
        with a 400. Other OpenAI-compatible servers (DeepSeek, Azure, local
        gateways) still expect ``max_tokens``, so only the real OpenAI endpoint
        switches.
        """
        if self._variant == "openai" and (
            self.endpoint is None or "api.openai.com" in self.endpoint.lower()
        ):
            return "max_completion_tokens"
        return "max_tokens"

    def _build_request(self, messages, tools):
        url, headers = self._url_and_headers()
        body: dict = {
            "model": self.model,
            "messages": _messages_to_openai(messages),
            self._max_tokens_field(): self.max_tokens,
        }
        oai_tools = _tools_to_openai(tools)
        if oai_tools:
            body["tools"] = oai_tools
            body["tool_choice"] = "auto"
        return "POST", url, headers, body

    def _parse_response(self, data: dict) -> ModelResponse:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = message.get("content") or ""
        tool_calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                arguments = as_arguments(json.loads(fn.get("arguments") or "{}"))
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                ToolCall(id=tc.get("id") or new_call_id(), name=fn.get("name", ""), arguments=arguments)
            )
        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=choice.get("finish_reason"),
            usage=data.get("usage"),
            raw=data,
        )
