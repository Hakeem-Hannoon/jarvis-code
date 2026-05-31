"""Gemini generateContent provider (Google AI Studio Gemini + Vertex AI).

Both speak the ``generateContent`` shape. Gemini authenticates with an API key
header (``x-goog-api-key`` — never the URL query param, to keep keys out of
logged URLs); Vertex uses a bearer token and a project/location URL.
"""

from __future__ import annotations

from .base import HTTPProvider
from .types import Message, ModelResponse, Role, ToolCall, ToolSpec

_GEMINI_BASE = "https://generativelanguage.googleapis.com"


def _messages_to_gemini(messages: list[Message]) -> tuple[dict | None, list[dict]]:
    system: dict | None = None
    contents: list[dict] = []
    for m in messages:
        if m.role == Role.SYSTEM:
            system = {"parts": [{"text": m.content}]}
        elif m.role == Role.TOOL:
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {"functionResponse": {"name": m.name or "", "response": {"content": m.content}}}
                    ],
                }
            )
        elif m.role == Role.ASSISTANT and m.tool_calls:
            parts: list[dict] = []
            if m.content:
                parts.append({"text": m.content})
            for tc in m.tool_calls:
                parts.append({"functionCall": {"name": tc.name, "args": tc.arguments}})
            contents.append({"role": "model", "parts": parts})
        else:
            role = "model" if m.role == Role.ASSISTANT else "user"
            contents.append({"role": role, "parts": [{"text": m.content}]})
    return system, contents


def _tools_to_gemini(tools: list[ToolSpec] | None) -> list[dict] | None:
    if not tools:
        return None
    return [
        {
            "functionDeclarations": [
                {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
                for t in tools
            ]
        }
    ]


class GeminiStyleProvider(HTTPProvider):
    def __init__(self, *, variant: str, **kwargs) -> None:
        self.name = variant
        self._variant = variant
        super().__init__(**kwargs)

    def _url_and_headers(self) -> tuple[str, dict]:
        if self._variant == "vertex":
            base = (self.endpoint or "").rstrip("/")
            url = (
                f"{base}/v1/projects/{self.project}/locations/{self.location}"
                f"/publishers/google/models/{self.model}:generateContent"
            )
            return url, {
                "Authorization": f"Bearer {self._api_key}",
                "content-type": "application/json",
            }
        base = (self.endpoint or _GEMINI_BASE).rstrip("/")
        url = f"{base}/v1beta/models/{self.model}:generateContent"
        return url, {"x-goog-api-key": self._api_key, "content-type": "application/json"}

    def _build_request(self, messages, tools):
        url, headers = self._url_and_headers()
        system, contents = _messages_to_gemini(messages)
        body: dict = {"contents": contents}
        if system:
            body["systemInstruction"] = system
        gemini_tools = _tools_to_gemini(tools)
        if gemini_tools:
            body["tools"] = gemini_tools
        return "POST", url, headers, body

    def _parse_response(self, data: dict) -> ModelResponse:
        candidate = (data.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for i, part in enumerate(parts):
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append(
                    ToolCall(id=f"call_{i}", name=fc.get("name", ""), arguments=fc.get("args", {}) or {})
                )
        return ModelResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=candidate.get("finishReason"),
            usage=data.get("usageMetadata"),
            raw=data,
        )
