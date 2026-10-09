"""Switching model / provider / effort / mode on a live session."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from jarvis_code.agent import assemble_runtime, split_model_spec
from jarvis_code.config import ConfigError, Effort, JarvisConfig
from jarvis_code.models import ModelResponse, ToolCall, build_provider
from jarvis_code.permissions import PermissionMode
from jarvis_code.sandbox import Sandbox


def _config(**voice) -> JarvisConfig:
    return JarvisConfig.model_validate(
        {
            "default_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "api_key": "sk-ant-000000",
                    "model": "claude-a",
                    "models": ["claude-b", "claude-c"],
                },
                "openai": {"api_key": "sk-oai-000000", "model": "gpt-a"},
                "deepseek": {"model": "deepseek-chat"},  # configured, but no key
            },
            "voice": {"enabled": False, **voice},
        }
    )


def _client(routes: dict[str, dict], seen: list | None = None) -> httpx.Client:
    """A mock transport: GET <substring> -> json; POSTs echo a canned reply."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.method == "GET":
            for needle, payload in routes.items():
                if needle in str(request.url):
                    return httpx.Response(200, json=payload)
            return httpx.Response(404, json={"error": "nope"})
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


def _runtime(sandbox: Sandbox, **kwargs):
    kwargs.setdefault("mode", PermissionMode.AUTO)
    kwargs.setdefault("http_client", _client({}))
    return assemble_runtime(config=_config(), sandbox=sandbox, **kwargs)


# -- model spec ---------------------------------------------------------------


def test_split_model_spec_only_splits_on_a_configured_provider() -> None:
    providers = {"anthropic", "openai"}
    assert split_model_spec("openai/gpt-5", providers) == ("openai", "gpt-5")
    assert split_model_spec("gpt-5", providers) == (None, "gpt-5")
    # A model id that merely contains a slash is left whole.
    assert split_model_spec("meta/llama-4", providers) == (None, "meta/llama-4")


# -- model / provider ---------------------------------------------------------


def test_switch_model_on_the_same_provider(sandbox: Sandbox) -> None:
    rt = _runtime(sandbox)
    settings = rt.controls.set_model("claude-b")
    assert (settings.provider, settings.model) == ("anthropic", "claude-b")
    assert rt.provider.model == "claude-b"


def test_switch_provider_and_model_in_one_spec(sandbox: Sandbox) -> None:
    rt = _runtime(sandbox)
    settings = rt.controls.set_model("openai/gpt-z")
    assert (settings.provider, settings.model) == ("openai", "gpt-z")
    assert rt.provider.name == "openai"
    # Sub-agents use the new provider too.
    assert rt.executor.ctx.orchestrator.provider is rt.provider


def test_switch_provider_uses_its_configured_model(sandbox: Sandbox) -> None:
    rt = _runtime(sandbox)
    assert rt.controls.set_provider("openai").model == "gpt-a"


def test_a_failed_switch_leaves_the_session_untouched(sandbox: Sandbox) -> None:
    rt = _runtime(sandbox)
    before = rt.provider
    with pytest.raises(ConfigError):
        rt.controls.set_provider("deepseek")  # configured but has no key
    with pytest.raises(ConfigError):
        rt.controls.set_provider("nope")
    assert rt.provider is before
    assert rt.controls.settings.provider == "anthropic"


async def test_the_next_turn_really_uses_the_new_model(sandbox: Sandbox) -> None:
    seen: list = []
    rt = _runtime(sandbox, http_client=_client({}, seen))
    rt.controls.set_model("claude-c")
    await rt.run("hello")
    import json

    assert json.loads(seen[-1].content)["model"] == "claude-c"


def test_providers_listing_never_exposes_keys(sandbox: Sandbox) -> None:
    infos = _runtime(sandbox).controls.providers()
    assert [i.name for i in infos] == ["anthropic", "deepseek", "openai"]  # current first
    assert {i.name: i.has_key for i in infos} == {"anthropic": True, "deepseek": False, "openai": True}
    assert "sk-" not in repr(infos)


# -- model discovery ----------------------------------------------------------


def test_known_models_need_no_network(sandbox: Sandbox) -> None:
    rt = _runtime(sandbox)
    assert rt.controls.known_models() == ["claude-a", "claude-b", "claude-c"]
    assert rt.controls.known_models("openai") == ["gpt-a"]


def test_fetch_models_asks_the_provider_and_caches(sandbox: Sandbox) -> None:
    seen: list = []
    client = _client({"/v1/models": {"data": [{"id": "claude-z"}, {"id": "claude-y"}]}}, seen)
    rt = _runtime(sandbox, http_client=client)
    assert rt.controls.fetch_models() == ["claude-y", "claude-z"]
    assert rt.controls.fetch_models() == ["claude-y", "claude-z"]
    assert len(seen) == 1  # second call was served from the cache
    # Fetched models now show up in the offline list as well.
    assert "claude-z" in rt.controls.known_models()


def test_fetch_models_is_empty_not_an_error_when_the_api_cannot_say(sandbox: Sandbox) -> None:
    rt = _runtime(sandbox)  # the mock answers 404 to every GET
    assert rt.controls.fetch_models() == []
    assert rt.controls.fetch_models("deepseek") == []  # no key: still no exception


def test_openai_listing_drops_models_that_cannot_chat() -> None:
    listing = {"data": [{"id": "gpt-5"}, {"id": "text-embedding-3-large"}, {"id": "whisper-1"}, {"id": "o3"}]}
    provider = build_provider(_config(), provider="openai", http_client=_client({"/models": listing}))
    assert provider.list_models() == ["gpt-5", "o3"]


def test_gemini_listing_keeps_only_generate_content_models() -> None:
    cfg = JarvisConfig.model_validate(
        {"default_provider": "gemini", "providers": {"gemini": {"api_key": "k-000000", "model": "g"}}}
    )
    listing = {
        "models": [
            {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        ]
    }
    provider = build_provider(cfg, http_client=_client({"/v1beta/models": listing}))
    assert provider.list_models() == ["gemini-3.8-flash"]


def test_model_listing_never_puts_the_key_in_the_url() -> None:
    seen: list = []
    provider = build_provider(_config(), provider="openai", http_client=_client({"/models": {"data": []}}, seen))
    provider.list_models()
    assert "sk-oai-000000" not in str(seen[0].url)


# -- effort -------------------------------------------------------------------


def test_switching_effort_updates_everything_that_depends_on_it(sandbox: Sandbox) -> None:
    rt = _runtime(sandbox)
    assert "spawn_subagent" not in rt.executor.registry
    rt.controls.set_effort(Effort.MADMAX)
    assert "spawn_subagent" in rt.executor.registry  # sub-agents unlock at madmax
    assert rt.provider.max_tokens == 8192
    assert "EFFORT madmax" in rt.context.system_prompt
    assert any(t["name"] == "spawn_subagent" for t in rt.executor.registry.specs())

    rt.controls.set_effort(Effort.LOW)
    assert "spawn_subagent" not in rt.executor.registry
    assert rt.max_steps == 15 and rt.provider.max_tokens == 4096
    assert "EFFORT low" in rt.context.system_prompt


def test_effort_survives_a_model_switch(sandbox: Sandbox) -> None:
    rt = _runtime(sandbox)
    rt.controls.set_effort(Effort.LOW)
    rt.controls.set_model("openai/gpt-z")
    assert rt.provider.max_tokens == 4096  # the new provider got the same budget
    assert rt.controls.settings.effort is Effort.LOW


# -- mode ---------------------------------------------------------------------


def test_switching_mode_also_lowers_the_subagent_ceiling(sandbox: Sandbox) -> None:
    # The orchestrator kept the mode it was built with, so a session lowered
    # from dangerous to plan still spawned dangerous sub-agents.
    rt = _runtime(sandbox, mode=PermissionMode.DANGEROUS, effort=Effort.MADMAX)
    rt.controls.set_mode(PermissionMode.PLAN)
    orchestrator = rt.executor.ctx.orchestrator
    assert orchestrator.effective_mode() is PermissionMode.PLAN
    assert orchestrator._build_subruntime("explorer").executor.permissions.mode is PermissionMode.PLAN
    assert "MODE: plan" in rt.context.system_prompt  # the model is told, too


async def test_mode_switch_is_enforced_on_the_next_tool_call(sandbox: Sandbox, tmp_path: Path) -> None:
    class Scripted:
        name, model = "scripted", "m"

        def __init__(self):
            self.n = 0

        def complete(self, messages, tools=None):
            self.n += 1
            if self.n == 1:
                return ModelResponse(tool_calls=[ToolCall("c", "write_file", {"path": "x.txt", "content": "x"})])
            return ModelResponse(text="done")

    rt = assemble_runtime(
        config=_config(), sandbox=sandbox, mode=PermissionMode.DANGEROUS, provider=Scripted()
    )
    rt.controls.set_mode(PermissionMode.PLAN)
    await rt.run("write it")
    assert not (tmp_path / "x.txt").exists()
