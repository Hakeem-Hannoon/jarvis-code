"""Security regression tests.

These pin the invariants behind three fixed findings:

* Provider API keys never enter model context (the conversation sent to the
  provider), even via untrusted tool output, the system prompt, or compaction.
* ``run_shell`` cannot read provider keys back out of the environment.
* The read-only git tools cannot be tricked into executing repo-controlled code.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis_code.agent.context import ConversationContext
from jarvis_code.agent.state import RunState
from jarvis_code.models import ModelResponse, ToolCall
from jarvis_code.sandbox import Sandbox
from jarvis_code.tools import ToolContext, build_default_registry
from jarvis_code.tools.git_tools import _GIT_HARDENING, _git_env
from jarvis_code.utils.logging import (
    REDACTION_MARKER,
    clear_secrets,
    register_secret,
    scrub_secret_env,
)

SECRET = "sk-ant-SUPER-secret-key-0123456789abcdef"  # not a real key


@pytest.fixture(autouse=True)
def _clean_secrets():
    clear_secrets()
    yield
    clear_secrets()


def _blob(ctx: ConversationContext) -> str:
    """All model-visible text the context would send to a provider."""
    return "\n".join(m.content for m in ctx.build())


# -- finding 2: keys must never reach model context -------------------------


def test_tool_output_redacted_in_context() -> None:
    register_secret(SECRET)
    ctx = ConversationContext("system prompt")
    # e.g. read_file(".jarvis/config.toml") returning the key.
    ctx.add_tool_result("id1", "read_file", f'api_key = "{SECRET}"\n')
    blob = _blob(ctx)
    assert SECRET not in blob
    assert REDACTION_MARKER in blob


def test_user_and_assistant_text_redacted_in_context() -> None:
    register_secret(SECRET)
    ctx = ConversationContext("sys")
    ctx.add_user(f"my key is {SECRET}")
    ctx.add_assistant(ModelResponse(text=f"ok using {SECRET}", tool_calls=[ToolCall("i", "t", {})]))
    blob = _blob(ctx)
    assert SECRET not in blob
    assert blob.count(REDACTION_MARKER) >= 2


def test_system_prompt_redacted_at_construction() -> None:
    register_secret(SECRET)
    ctx = ConversationContext(f"You are jarvis. key={SECRET}")
    assert SECRET not in ctx.build()[0].content
    assert REDACTION_MARKER in ctx.build()[0].content


def test_compaction_summary_is_redacted() -> None:
    register_secret(SECRET)
    state = RunState()
    state.goal = f"deploy using {SECRET}"  # raw secret reaches the summary
    ctx = ConversationContext("sys", max_chars=10, keep_recent=2)
    for i in range(6):
        ctx.add_user(f"message number {i}")
    assert ctx.needs_compaction()
    assert ctx.compact(state) is True
    blob = _blob(ctx)
    assert SECRET not in blob
    assert REDACTION_MARKER in blob


# -- finding 3: scrub secrets from any subprocess environment ---------------


def test_scrub_secret_env_drops_by_name_and_value() -> None:
    register_secret(SECRET)
    env = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": SECRET,   # credential-shaped name
        "FOO_TOKEN": "abcdefgh",     # credential-shaped name
        "DB_PASSWORD": "hunter2!!",  # credential-shaped name
        "ODD_NAME": SECRET,          # registered value, innocuous name
        "SAFE": "ok",
    }
    out = scrub_secret_env(env)
    assert out["PATH"] == "/usr/bin"
    assert out["SAFE"] == "ok"
    for dropped in ("OPENAI_API_KEY", "FOO_TOKEN", "DB_PASSWORD", "ODD_NAME"):
        assert dropped not in out


def test_run_shell_does_not_leak_keys_from_env(tmp_path: Path, monkeypatch) -> None:
    register_secret(SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)  # name + value
    monkeypatch.setenv("CUSTOM_THING", SECRET)        # value only
    ctx = ToolContext(sandbox=Sandbox(tmp_path), shell_timeout=20.0)
    tool = build_default_registry().get("run_shell")
    res = tool.run(ctx, {"command": "env"})
    assert res.ok
    assert SECRET not in res.output
    assert "ANTHROPIC_API_KEY" not in res.output
    assert "PATH=" in res.output  # ordinary environment is preserved


# -- finding 4: git tools cannot execute repo-controlled code ---------------


def test_git_hardening_present() -> None:
    joined = " ".join(_GIT_HARDENING)
    assert "--no-pager" in _GIT_HARDENING
    assert "core.fsmonitor=" in joined  # disables status-time program execution
    # diff-time program execution (external/textconv) is disabled by flags on
    # `git diff` itself — see test_git_diff_does_not_run_repo_textconv.


def test_git_env_is_hardened(monkeypatch) -> None:
    register_secret(SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "evil-program")
    monkeypatch.setenv("GIT_PAGER", "evil-pager")
    env = _git_env()
    assert env.get("GIT_CONFIG_NOSYSTEM") == "1"
    assert env.get("GIT_TERMINAL_PROMPT") == "0"
    assert "GIT_EXTERNAL_DIFF" not in env
    assert "GIT_PAGER" not in env
    assert "ANTHROPIC_API_KEY" not in env


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_diff_does_not_run_repo_textconv(tmp_path: Path) -> None:
    """A hostile repo configuring a textconv diff driver must not get code-exec."""
    root = tmp_path

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    pwned = root / "PWNED"
    # git invokes `<textconv> <path>`; the trailing '#' comments the path out.
    git("config", "diff.evil.textconv", f"sh -c 'touch {pwned}' #")
    (root / ".gitattributes").write_text("* diff=evil\n")
    (root / "f.txt").write_text("one\n")
    git("add", ".")
    git("commit", "-qm", "init")
    (root / "f.txt").write_text("two\n")  # an unstaged change to diff

    ctx = ToolContext(sandbox=Sandbox(root), shell_timeout=20.0)
    res = build_default_registry().get("git_diff").run(ctx, {})
    assert res.ok
    assert not pwned.exists()  # textconv filter must NOT have executed
