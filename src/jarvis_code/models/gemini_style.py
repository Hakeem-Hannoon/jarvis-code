"""Gemini generateContent provider (Google AI Studio Gemini + Vertex AI).

Both speak the ``generateContent`` shape. Gemini authenticates with an API key
header (``x-goog-api-key`` — never the URL query param, to keep keys out of
logged URLs); Vertex uses a bearer token and a project/location URL.
"""

from __future__ import annotations

from .base import HTTPProvider, as_arguments
from .types import Message, ModelResponse, Role, ToolCall, ToolSpec

_GEMINI_BASE = "https://generativelanguage.googleapis.com"


def _messages_to_gemini(messages: list[Message]) -> tuple[dict | None, list[dict]]:
    system: dict | None = None
    contents: list[dict] = []
    for m in messages:
        if m.role == Role.SYSTEM:
            system = {"parts": [{"text": m.content}]}
        elif m.role == Role.TOOL:
            part = {
                "functionResponse": {
                    "name": m.name or "",
                    "response": {"content": m.content or "(no output)"},
                }
            }
            # Gemini requires ONE turn holding as many functionResponse parts as
            # the model turn had functionCall parts; a turn per result is a 400
            # as soon as the model calls two tools at once.
            last = contents[-1] if contents else None
            if (
                last is not None
                and last["role"] == "user"
                and all("functionResponse" in p for p in last["parts"])
            ):
                last["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
        elif m.role == Role.ASSISTANT and m.tool_calls:
            parts: list[dict] = []
            if m.content:
                parts.append({"text": m.content})
            for tc in m.tool_calls:
                call_part: dict = {"functionCall": {"name": tc.name, "args": tc.arguments}}
                signature = tc.metadata.get("thought_signature")
                if signature:
                    # Thinking models require the signature echoed back verbatim.
                    call_part["thoughtSignature"] = signature
                parts.append(call_part)
            contents.append({"role": "model", "parts": parts})
        elif not m.content.strip():
            continue  # an empty text part is rejected; an empty turn says nothing
        else:
            role = "model" if m.role == Role.ASSISTANT else "user"
            contents.append({"role": role, "parts": [{"text": m.content}]})
    return system, contents


def _declaration(tool: ToolSpec) -> dict:
    decl = {"name": tool["name"], "description": tool["description"]}
    # An OBJECT schema with no properties is rejected; a no-argument function
    # is declared by simply omitting ``parameters``.
    if (tool.get("parameters") or {}).get("properties"):
        decl["parameters"] = tool["parameters"]
    return decl


def _tools_to_gemini(tools: list[ToolSpec] | None) -> list[dict] | None:
    if not tools:
        return None
    return [{"functionDeclarations": [_declaration(t) for t in tools]}]


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

    def _models_request(self):
        if self._variant == "vertex":
            return None  # publisher models are not listable with this auth
        base = (self.endpoint or _GEMINI_BASE).rstrip("/")
        return f"{base}/v1beta/models?pageSize=1000", {"x-goog-api-key": self._api_key}

    def _parse_models(self, data: dict) -> list[str]:
        out = []
        for m in data.get("models") or []:
            if not isinstance(m, dict):
                continue
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue  # embedding / imagen / etc.
            name = str(m.get("name", ""))
            out.append(name.removeprefix("models/"))
        return out

    def _build_request(self, messages, tools):
        url, headers = self._url_and_headers()
        system, contents = _messages_to_gemini(messages)
        # No maxOutputTokens: on Gemini's thinking models the cap also counts
        # reasoning tokens, so a modest budget can come back as an empty reply.
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
            if "functionCall" in part:
                fc = part["functionCall"] or {}
                metadata = {}
                if part.get("thoughtSignature"):
                    metadata["thought_signature"] = part["thoughtSignature"]
                tool_calls.append(
                    ToolCall(
                        id=f"call_{i}",
                        name=fc.get("name", ""),
                        arguments=as_arguments(fc.get("args")),
                        metadata=metadata,
                    )
                )
            elif "text" in part and not part.get("thought"):
                # Skip "thought" parts: internal reasoning summaries, not the reply.
                text_parts.append(part["text"])
        return ModelResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=candidate.get("finishReason"),
            usage=data.get("usageMetadata"),
            raw=data,
        )
