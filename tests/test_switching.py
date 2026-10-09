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


# =============================================================================
# The chat commands (/model, /provider, /effort, /mode, /save) and their UI
# =============================================================================

import re  # noqa: E402

from jarvis_code.cli.configedit import save_defaults, updated_config_text  # noqa: E402
from jarvis_code.cli.picker import Option, filter_options, pick  # noqa: E402
from jarvis_code.cli.settings import (  # noqa: E402
    completions,
    handle_effort,
    handle_mode,
    handle_model,
    handle_provider,
    handle_save,
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(lines: list[str]) -> str:
    return _ANSI.sub("", "\n".join(lines))


def _never(*_a, **_k):
    raise AssertionError("the picker must not open when a value was given")


def _choose(value):
    """A stand-in picker that records what it was offered and returns ``value``."""
    calls: list[dict] = []

    def chooser(title, options, **kwargs):
        calls.append({"title": title, "options": list(options), **kwargs})
        return value

    chooser.calls = calls  # type: ignore[attr-defined]
    return chooser


# -- /model -------------------------------------------------------------------


def test_model_command_with_a_value_switches_without_a_picker(sandbox: Sandbox) -> None:
    rt, out = _runtime(sandbox), []
    handle_model(rt, "claude-b", out.append, chooser=_never)
    assert rt.provider.model == "claude-b"
    assert "model → anthropic · claude-b" in _plain(out)
    assert "/save" in _plain(out)  # says how to keep it


def test_model_command_accepts_provider_slash_model(sandbox: Sandbox) -> None:
    rt, out = _runtime(sandbox), []
    handle_model(rt, "openai/gpt-z", out.append, chooser=_never)
    assert (rt.provider.name, rt.provider.model) == ("openai", "gpt-z")


def test_bare_model_command_opens_a_picker_with_live_and_cross_provider_models(
    sandbox: Sandbox,
) -> None:
    client = _client({"/v1/models": {"data": [{"id": "claude-live"}]}})
    rt, out = _runtime(sandbox, http_client=client), []
    chooser = _choose("openai/gpt-a")
    handle_model(rt, "", out.append, chooser=chooser)

    call = chooser.calls[0]
    values = [o.value for o in call["options"]]
    # this provider: in use, favourites, fetched live …
    assert values[:4] == ["claude-a", "claude-b", "claude-c", "claude-live"]
    # … then other providers that have a key (deepseek has none: not offered)
    assert "openai/gpt-a" in values and not any(v.startswith("deepseek/") for v in values)
    assert call["current"] == "claude-a" and call["allow_custom"] is True
    assert (rt.provider.name, rt.provider.model) == ("openai", "gpt-a")


def test_cancelling_the_picker_changes_nothing(sandbox: Sandbox) -> None:
    rt, out = _runtime(sandbox), []
    handle_model(rt, "", out.append, chooser=_choose(None))
    assert rt.provider.model == "claude-a"
    assert "unchanged" in _plain(out)


def test_a_bad_switch_is_explained_not_raised(sandbox: Sandbox) -> None:
    rt, out = _runtime(sandbox), []
    handle_provider(rt, "deepseek", out.append, chooser=_never)  # configured, no key
    assert rt.controls.settings.provider == "anthropic"
    assert "No API key found for provider 'deepseek'" in _plain(out)
    handle_provider(rt, "nope", out.append, chooser=_never)
    assert "not configured" in _plain(out)


def test_provider_picker_shows_key_status_without_the_key(sandbox: Sandbox) -> None:
    rt, out = _runtime(sandbox), []
    chooser = _choose("openai")
    handle_provider(rt, "", out.append, chooser=chooser)
    hints = {o.value: o.hint for o in chooser.calls[0]["options"]}
    assert "no key — set DEEPSEEK_API_KEY" in hints["deepseek"]
    assert "sk-" not in repr(chooser.calls[0]["options"])
    assert rt.provider.name == "openai"


# -- /effort, /mode -----------------------------------------------------------


def test_effort_command(sandbox: Sandbox) -> None:
    rt, out = _runtime(sandbox), []
    handle_effort(rt, "HIGH", out.append, chooser=_never)
    assert rt.controls.settings.effort is Effort.HIGH
    handle_effort(rt, "turbo", out.append, chooser=_never)
    assert "usage: /effort low|medium|high|madmax" in _plain(out)
    handle_effort(rt, "", out.append, chooser=_choose("madmax"))
    assert rt.controls.settings.effort is Effort.MADMAX


def test_mode_command_goes_through_the_controls(sandbox: Sandbox) -> None:
    rt, out = _runtime(sandbox, mode=PermissionMode.DANGEROUS), []
    handle_mode(rt, "", out.append, chooser=_choose("plan"))
    assert rt.executor.permissions.mode is PermissionMode.PLAN
    assert rt.executor.ctx.orchestrator.parent_mode is PermissionMode.PLAN  # re-clamped
    assert any("[mode changed to plan]" in m.content for m in rt.context.messages)


# -- /save --------------------------------------------------------------------

_CONFIG_TEXT = '''\
# my jarvis config — comments must survive a /save
default_provider = "anthropic"   # the one I use most

[providers.anthropic]
api_key = "sk-ant-SECRET-123456"              # keep me
model = "claude-a"                            # default model
models = ["claude-b"]

[providers.openai]
api_key = "sk-oai-SECRET-654321"
model = "gpt-a"

[voice]
enabled = false
'''


def test_save_changes_three_values_and_nothing_else() -> None:
    new = updated_config_text(_CONFIG_TEXT, provider="openai", model="gpt-z", effort=Effort.HIGH)
    assert 'default_provider = "openai"   # the one I use most' in new  # comment kept
    assert 'model = "gpt-z"' in new and 'effort = "high"' in new
    # Everything else is byte-for-byte what the user wrote.
    for line in _CONFIG_TEXT.splitlines():
        if not line.startswith(("default_provider", 'model = "gpt-a"')):
            assert line in new.splitlines(), line
    assert 'model = "claude-a"' in new  # the OTHER provider's model is untouched


def test_save_is_idempotent_and_updates_an_existing_effort() -> None:
    once = updated_config_text(_CONFIG_TEXT, provider="openai", model="gpt-z", effort=Effort.HIGH)
    twice = updated_config_text(once, provider="openai", model="gpt-z", effort=Effort.LOW)
    assert twice.count("[agent]") == 1 and 'effort = "low"' in twice and 'effort = "high"' not in twice


def test_save_refuses_rather_than_guess(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):  # no such provider section in this file
        updated_config_text(_CONFIG_TEXT, provider="gemini", model="g", effort=Effort.LOW)
    with pytest.raises(ConfigError):  # a layout the line editor can't handle safely
        updated_config_text(
            'default_provider = "anthropic"\nproviders = { anthropic = { api_key = "sk-000000", model = "a" } }\n',
            provider="anthropic", model="b", effort=Effort.LOW,
        )


def test_save_command_writes_the_file_and_keeps_it_private(sandbox: Sandbox, tmp_path: Path) -> None:
    import os
    import stat

    path = tmp_path / "config.toml"
    path.write_text(_CONFIG_TEXT)
    path.chmod(0o600)
    rt, out = _runtime(sandbox), []
    rt.controls.set_model("openai/gpt-z")
    rt.controls.set_effort(Effort.HIGH)
    handle_save(rt, out.append, config_path=path)

    saved = JarvisConfig.model_validate(__import__("tomllib").loads(path.read_text()))
    assert saved.default_provider == "openai"
    assert saved.providers["openai"].model == "gpt-z" and saved.agent.effort is Effort.HIGH
    assert saved.providers["anthropic"].api_key.get_secret_value() == "sk-ant-SECRET-123456"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600  # still owner-only
    assert "saved as default" in _plain(out) and "SECRET" not in _plain(out)
    assert not list(tmp_path.glob(".config-*"))  # no temp file left behind


def test_save_defaults_leaves_the_file_alone_when_it_would_break(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(_CONFIG_TEXT)
    with pytest.raises(ConfigError):
        save_defaults(path, provider="gemini", model="g", effort=Effort.LOW)
    assert path.read_text() == _CONFIG_TEXT


# -- picker -------------------------------------------------------------------

_OPTIONS = [Option("claude-opus"), Option("claude-sonnet"), Option("openai/gpt-5", label="gpt-5", hint="openai")]


def test_filter_matches_every_term_across_label_and_hint() -> None:
    assert [o.value for o in filter_options(_OPTIONS, "claude son")] == ["claude-sonnet"]
    assert [o.value for o in filter_options(_OPTIONS, "OPENAI")] == ["openai/gpt-5"]
    assert filter_options(_OPTIONS, "") == _OPTIONS


@pytest.mark.parametrize(
    "typed,expected",
    [
        ("2", "claude-sonnet"),      # by number
        ("gpt-5", "openai/gpt-5"),   # by label
        ("opus", "claude-opus"),     # a unique partial match
        ("", None),                  # blank cancels
        ("claude", None),            # ambiguous and custom not allowed
    ],
)
def test_picker_typed_fallback(typed: str, expected: str | None) -> None:
    shown: list[str] = []
    assert pick("model", _OPTIONS, input_fn=lambda _p: typed, output_fn=shown.append) == expected
    assert any("claude-opus" in line for line in shown)


def test_picker_can_take_a_name_that_is_not_listed() -> None:
    got = pick("model", _OPTIONS, allow_custom=True,
               input_fn=lambda _p: "my-azure-deployment", output_fn=lambda _s: None)
    assert got == "my-azure-deployment"


def test_picker_labels_cannot_inject_terminal_escapes() -> None:
    shown: list[str] = []
    pick("model", [Option("evil\x1b[2Jmodel")], input_fn=lambda _p: "", output_fn=shown.append)
    assert "\x1b[2J" not in "\n".join(shown)


# -- tab completion -----------------------------------------------------------


def test_completions(sandbox: Sandbox) -> None:
    rt = _runtime(sandbox)
    assert completions("/mo", rt) == ["/model", "/mode"]
    assert completions("/effort h", rt) == ["high"]
    assert completions("/mode ", rt) == ["plan", "auto", "dangerous"]
    assert completions("/provider o", rt) == ["openai"]
    assert completions("/model claude-", rt) == ["claude-a", "claude-b", "claude-c"]
    assert completions("/model open", rt) == ["openai/gpt-a"]
    assert completions("fix the bug in /mo", rt) == []  # only for slash commands
    assert completions("/model a b", rt) == []


def test_completion_never_calls_the_network(sandbox: Sandbox) -> None:
    seen: list = []
    rt = _runtime(sandbox, http_client=_client({}, seen))
    completions("/model ", rt)
    assert seen == []


# -- launch flags -------------------------------------------------------------


def test_model_flag_can_name_the_provider_too() -> None:
    from jarvis_code.cli.options import GlobalOptions
    from jarvis_code.cli.run import resolve_model_flags

    cfg = _config()
    assert resolve_model_flags(GlobalOptions(model="openai/gpt-z"), cfg) == ("openai", "gpt-z")
    assert resolve_model_flags(GlobalOptions(model="gpt-z"), cfg) == (None, "gpt-z")
    assert resolve_model_flags(GlobalOptions(provider="openai"), cfg) == ("openai", None)
    # An explicit --provider wins; the prefix is still stripped from the model.
    assert resolve_model_flags(GlobalOptions(provider="anthropic", model="openai/x"), cfg) == ("anthropic", "x")
    assert resolve_model_flags(GlobalOptions(model="meta/llama"), cfg) == (None, "meta/llama")


def test_short_flags_route_like_the_long_ones() -> None:
    from jarvis_code.cli.main import inject_default_command

    assert inject_default_command(["-m", "openai/gpt-5", "-e", "high", "fix it"]) == [
        "-m", "openai/gpt-5", "-e", "high", "run", "fix it"
    ]
    assert inject_default_command(["-p", "openai", "models"]) == ["-p", "openai", "models"]
    assert inject_default_command(["models"]) == ["models"]


def test_models_command_lists_without_printing_keys(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from jarvis_code.cli.main import app

    (tmp_path / ".jarvis.toml").write_text(_CONFIG_TEXT)
    result = CliRunner().invoke(app, ["--sandbox", str(tmp_path), "models", "--offline"])
    assert result.exit_code == 0
    assert "anthropic" in result.output and "claude-a" in result.output and "claude-b" in result.output
    assert "SECRET" not in result.output


# -- mixing providers in one conversation -------------------------------------


def test_gemini_tool_call_ids_are_unique_across_turns() -> None:
    cfg = JarvisConfig.model_validate(
        {"default_provider": "gemini", "providers": {"gemini": {"api_key": "k-000000", "model": "g"}}}
    )
    reply = {"candidates": [{"content": {"parts": [
        {"functionCall": {"name": "list_files", "args": {}}},
        {"functionCall": {"name": "git_status", "args": {}}},
    ]}}]}
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=reply)))
    provider = build_provider(cfg, http_client=client)
    from jarvis_code.models import Message

    ids = [tc.id for _ in range(3) for tc in provider.complete([Message.user("x")]).tool_calls]
    # Positional ids ("call_0", "call_1" every turn) collide as soon as the
    # history is replayed to a provider that requires unique tool-use ids.
    assert len(set(ids)) == 6


async def test_history_with_tool_calls_survives_a_provider_switch(sandbox: Sandbox, tmp_path: Path) -> None:
    import json

    (tmp_path / "a.txt").write_text("hi\n")
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if "/v1/messages" in str(request.url):  # anthropic-shaped
            last = body["messages"][-1]["content"]
            if isinstance(last, str) and last == "read it":
                return httpx.Response(200, json={"content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "a.txt"}}]})
            return httpx.Response(200, json={"content": [{"type": "text", "text": "read"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "still with you"}}]})

    rt = _runtime(sandbox, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    await rt.run("read it")  # anthropic turn, with a tool call + result in history
    rt.controls.set_model("openai/gpt-a")
    result = await rt.run("and now?")
    assert result.final_text == "still with you"

    sent = json.loads(seen[-1].content)["messages"]  # what openai was shown
    call = next(m for m in sent if m.get("tool_calls"))
    answer = next(m for m in sent if m["role"] == "tool")
    assert call["tool_calls"][0]["id"] == answer["tool_call_id"] == "toolu_1"
    assert sent[0]["role"] == "system" and "MODE: auto" in sent[0]["content"]
