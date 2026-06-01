"""Phase 4 provider tests: selection, per-provider wiring (mocked HTTP), and the
invariant that API keys are used only in headers — never in the model context,
body, or URL.
"""

from __future__ import annotations

import json

import httpx
import pytest

from jarvis_code.config import JarvisConfig, MissingAPIKeyError
from jarvis_code.models import (
    AnthropicProvider,
    GeminiStyleProvider,
    Message,
    OpenAIStyleProvider,
    ProviderError,
    build_provider,
)

KEYS = {
    "anthropic": "sk-ant-AAAAAAAAAAAAAAAAAA",
    "openai": "sk-oai-BBBBBBBBBBBBBBBBBB",
    "deepseek": "sk-ds-CCCCCCCCCCCCCCCCCC",
    "gemini": "AIzaG-DDDDDDDDDDDDDDDDDD",
    "vertex": "ya29-VVVVVVVVVVVVVVVVVV",
    "azure": "azkey-ZZZZZZZZZZZZZZZZZZ",
}

EXPECTED_TYPE = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIStyleProvider,
    "deepseek": OpenAIStyleProvider,
    "gemini": GeminiStyleProvider,
    "vertex": GeminiStyleProvider,
    "azure": OpenAIStyleProvider,
}

# Which (lowercased) header carries the key for each provider.
AUTH_HEADER = {
    "anthropic": "x-api-key",
    "openai": "authorization",
    "deepseek": "authorization",
    "gemini": "x-goog-api-key",
    "vertex": "authorization",
    "azure": "api-key",
}


def make_config(default: str = "anthropic") -> JarvisConfig:
    return JarvisConfig.model_validate(
        {
            "default_provider": default,
            "providers": {
                "anthropic": {"api_key": KEYS["anthropic"], "model": "claude-opus-4-8"},
                "openai": {"api_key": KEYS["openai"], "model": "gpt-4o"},
                "deepseek": {"api_key": KEYS["deepseek"], "model": "deepseek-chat"},
                "gemini": {"api_key": KEYS["gemini"], "model": "gemini-1.5-pro"},
                "vertex": {
                    "api_key": KEYS["vertex"],
                    "model": "gemini-1.5-pro",
                    "endpoint": "https://vertex.example.com",
                    "project": "proj-x",
                    "location": "us-central1",
                },
                "azure": {
                    "api_key": KEYS["azure"],
                    "model": "my-deployment",
                    "endpoint": "https://res.openai.azure.com",
                    "api_version": "2024-06-01",
                },
            },
        }
    )


def mock_client(captured: list, response_json: dict, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status, json=response_json)

    return httpx.Client(transport=httpx.MockTransport(handler))


# -- selection --------------------------------------------------------------


def test_selects_default_provider() -> None:
    provider = build_provider(make_config(), http_client=httpx.Client())
    assert provider.name == "anthropic"
    assert provider.model == "claude-opus-4-8"


def test_flag_overrides_provider_and_model() -> None:
    provider = build_provider(make_config(), provider="openai", model="gpt-4o-mini")
    assert provider.name == "openai"
    assert provider.model == "gpt-4o-mini"


@pytest.mark.parametrize("name", list(EXPECTED_TYPE))
def test_correct_client_per_provider(name: str) -> None:
    provider = build_provider(make_config(), provider=name)
    assert isinstance(provider, EXPECTED_TYPE[name])
    assert provider.name == name


def test_missing_key_raises() -> None:
    config = JarvisConfig.model_validate(
        {"default_provider": "openai", "providers": {"openai": {"api_key": "", "model": "gpt-4o"}}}
    )
    with pytest.raises(MissingAPIKeyError):
        build_provider(config, provider="openai")


# -- key isolation (the critical invariant) ---------------------------------


@pytest.mark.parametrize("name", list(EXPECTED_TYPE))
def test_key_only_in_headers_never_in_context(name: str) -> None:
    captured: list[httpx.Request] = []
    # A benign empty-ish response valid for each parser shape.
    response = {"content": [], "choices": [{"message": {}}], "candidates": [{}]}
    client = mock_client(captured, response)
    provider = build_provider(make_config(), provider=name, http_client=client)

    messages = [
        Message.system("You are a coding agent."),
        Message.user("List the files."),
        Message.tool_result("call_1", "list_files", "src/\nREADME.md"),
    ]
    tools = [{"name": "list_files", "description": "list", "parameters": {"type": "object"}}]
    provider.complete(messages, tools)

    assert len(captured) == 1
    request = captured[0]
    key = KEYS[name]

    # 1) The key IS present in exactly the expected auth header (it's used).
    header_blob = "\n".join(f"{k}: {v}" for k, v in request.headers.items())
    assert key in request.headers.get(AUTH_HEADER[name], ""), header_blob

    # 2) The key is NEVER in the URL (keeps it out of logged URLs).
    assert key not in str(request.url)

    # 3) The key is NEVER in the request body (the model context).
    body = request.content.decode()
    assert key not in body

    # 4) The key is NEVER in any message object the model can see.
    for m in messages:
        assert key not in json.dumps(m.content)


def test_repr_does_not_leak_key() -> None:
    provider = build_provider(make_config(), provider="anthropic")
    assert KEYS["anthropic"] not in repr(provider)


# -- response parsing per style ---------------------------------------------


def test_openai_parses_text_and_tool_calls() -> None:
    captured: list = []
    response = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "Reading now.",
                    "tool_calls": [
                        {
                            "id": "call_42",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'},
                        }
                    ],
                },
            }
        ],
        "usage": {"total_tokens": 10},
    }
    provider = build_provider(make_config(), provider="openai", http_client=mock_client(captured, response))
    result = provider.complete([Message.user("read a.txt")])
    assert result.text == "Reading now."
    assert result.wants_tools
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "a.txt"}
    assert result.stop_reason == "tool_calls"


def test_anthropic_parses_text_and_tool_use() -> None:
    captured: list = []
    response = {
        "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "tu_1", "name": "list_files", "input": {"path": "."}},
        ],
    }
    provider = build_provider(make_config(), provider="anthropic", http_client=mock_client(captured, response))
    result = provider.complete([Message.user("list")])
    assert result.text == "Let me look."
    assert result.tool_calls[0].name == "list_files"
    assert result.tool_calls[0].arguments == {"path": "."}


def test_gemini_parses_text_and_function_call() -> None:
    captured: list = []
    response = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {"text": "Searching."},
                        {"functionCall": {"name": "grep_text", "args": {"pattern": "TODO"}}},
                    ]
                },
            }
        ]
    }
    provider = build_provider(make_config(), provider="gemini", http_client=mock_client(captured, response))
    result = provider.complete([Message.user("find TODOs")])
    assert result.text == "Searching."
    assert result.tool_calls[0].name == "grep_text"
    assert result.tool_calls[0].arguments == {"pattern": "TODO"}


# -- per-provider URL shape -------------------------------------------------


def test_azure_url_uses_deployment_and_api_version() -> None:
    captured: list = []
    provider = build_provider(
        make_config(), provider="azure", http_client=mock_client(captured, {"choices": [{"message": {}}]})
    )
    provider.complete([Message.user("hi")])
    url = str(captured[0].url)
    assert "/openai/deployments/my-deployment/chat/completions" in url
    assert "api-version=2024-06-01" in url


def test_azure_foundry_url_uses_models_route_with_model_in_body() -> None:
    captured: list = []
    config = JarvisConfig.model_validate(
        {
            "default_provider": "azure",
            "providers": {
                "azure": {
                    "api_key": KEYS["azure"],
                    "model": "gpt-4o",
                    "endpoint": "https://my-res.services.ai.azure.com",
                    "api_version": "2024-05-01-preview",
                }
            },
        }
    )
    provider = build_provider(
        config, provider="azure", http_client=mock_client(captured, {"choices": [{"message": {}}]})
    )
    provider.complete([Message.user("hi")])
    url = str(captured[0].url)
    # Foundry shape: /models/chat/completions, model carried in the body.
    assert "/models/chat/completions" in url
    assert "/openai/deployments/" not in url
    assert json.loads(captured[0].content.decode())["model"] == "gpt-4o"
    assert KEYS["azure"] in captured[0].headers.get("api-key", "")
    assert KEYS["azure"] not in url


def test_azure_endpoint_style_forces_foundry() -> None:
    captured: list = []
    config = JarvisConfig.model_validate(
        {
            "default_provider": "azure",
            "providers": {
                "azure": {
                    "api_key": KEYS["azure"],
                    "model": "my-deployment",
                    "endpoint": "https://my-res.openai.azure.com",
                    "endpoint_style": "foundry",
                }
            },
        }
    )
    provider = build_provider(
        config, provider="azure", http_client=mock_client(captured, {"choices": [{"message": {}}]})
    )
    provider.complete([Message.user("hi")])
    assert "/models/chat/completions" in str(captured[0].url)


def test_vertex_url_uses_project_and_location() -> None:
    captured: list = []
    provider = build_provider(
        make_config(), provider="vertex", http_client=mock_client(captured, {"candidates": [{}]})
    )
    provider.complete([Message.user("hi")])
    url = str(captured[0].url)
    assert "/projects/proj-x/locations/us-central1/" in url
    assert url.endswith(":generateContent")


# -- error handling ---------------------------------------------------------


def test_provider_error_on_http_error_has_no_key() -> None:
    captured: list = []
    client = mock_client(captured, {"error": "bad request"}, status=400)
    provider = build_provider(make_config(), provider="anthropic", http_client=client)
    with pytest.raises(ProviderError) as exc:
        provider.complete([Message.user("hi")])
    assert KEYS["anthropic"] not in str(exc.value)
