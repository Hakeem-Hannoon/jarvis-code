"""Anthropic Messages API provider."""

from __future__ import annotations

from .base import HTTPProvider, as_arguments
from .types import Message, ModelResponse, Role, ToolCall, ToolSpec

_DEFAULT_BASE = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"


def _messages_to_anthropic(messages: list[Message]) -> tuple[str | None, list[dict]]:
    system_parts: list[str] = []
    out: list[dict] = []
    for m in messages:
        if m.role == Role.SYSTEM:
            system_parts.append(m.content)
        elif m.role == Role.TOOL:
            block = {
                "type": "tool_result",
                "tool_use_id": m.tool_call_id,
                "content": m.content or "(no output)",
            }
            # All results for one assistant turn belong in ONE user message,
            # directly after it — so append to the previous results message.
            last = out[-1] if out else None
            if (
                last is not None
                and last["role"] == "user"
                and isinstance(last["content"], list)
                and all(b.get("type") == "tool_result" for b in last["content"])
            ):
                last["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m.role == Role.ASSISTANT and m.tool_calls:
            blocks: list[dict] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                )
            out.append({"role": "assistant", "content": blocks})
        elif not m.content.strip():
            # The API rejects empty text content. An empty turn carries nothing
            # (e.g. a model reply with no text), so leave it out.
            continue
        else:
            out.append({"role": m.role.value, "content": m.content})
    system = "\n\n".join(p for p in system_parts if p) or None
    return system, out


def _tools_to_anthropic(tools: list[ToolSpec] | None) -> list[dict] | None:
    if not tools:
        return None
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
        for t in tools
    ]


class AnthropicProvider(HTTPProvider):
    name = "anthropic"

    def _build_request(self, messages, tools):
        base = (self.endpoint or _DEFAULT_BASE).rstrip("/")
        system, msgs = _messages_to_anthropic(messages)
        body: dict = {"model": self.model, "max_tokens": self.max_tokens, "messages": msgs}
        if system:
            body["system"] = system
        anthropic_tools = _tools_to_anthropic(tools)
        if anthropic_tools:
            body["tools"] = anthropic_tools
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }
        return "POST", f"{base}/v1/messages", headers, body

    def _parse_response(self, data: dict) -> ModelResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=as_arguments(block.get("input")),
                    )
                )
        return ModelResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=data.get("stop_reason"),
            usage=data.get("usage"),
            raw=data,
        )
