"""Regression tests for bugs found in the audit (one section per fix)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_code.agent import assemble_runtime
from jarvis_code.agent.loop import RunStatus
from jarvis_code.config import JarvisConfig
from jarvis_code.models import ModelResponse, ToolCall
from jarvis_code.permissions import OutcomeKind, PermissionManager, PermissionMode
from jarvis_code.sandbox import Sandbox
from jarvis_code.tools import ToolContext, ToolExecutor, build_default_registry


class ScriptedProvider:
    """Returns canned responses in order and records what it was sent."""

    name = "scripted"
    model = "test"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list] = []

    def complete(self, messages, tools=None) -> ModelResponse:
        self.calls.append(list(messages))
        return self._responses.pop(0)


def _config() -> JarvisConfig:
    return JarvisConfig.model_validate(
        {
            "default_provider": "anthropic",
            "providers": {"anthropic": {"api_key": "sk-test-000000", "model": "m"}},
            "voice": {"enabled": False},
        }
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("hello\nworld\n")
    return tmp_path


@pytest.fixture
def sandbox(root: Path) -> Sandbox:
    return Sandbox(root)


@pytest.fixture
def executor(sandbox: Sandbox) -> ToolExecutor:
    return ToolExecutor(
        build_default_registry(),
        PermissionManager(PermissionMode.DANGEROUS),
        ToolContext(sandbox=sandbox, shell_timeout=10.0),
    )


# -- a tool failure is a tool result, never a crashed run --------------------


def test_missing_argument_is_a_failed_result_not_an_exception(executor) -> None:
    out = executor.execute("read_file", {})
    assert out.outcome is OutcomeKind.ALLOWED
    assert out.result is not None and not out.result.ok
    assert "missing required argument 'path'" in (out.result.error or "")


def test_non_object_arguments_are_rejected_cleanly(executor) -> None:
    out = executor.execute("read_file", ["not", "a", "dict"])  # type: ignore[arg-type]
    assert out.result is not None and not out.result.ok
    assert executor.preview("read_file", "nope") is None  # type: ignore[arg-type]


def test_overwriting_a_binary_file_does_not_crash(executor, root: Path) -> None:
    (root / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
    out = executor.execute("write_file", {"path": "blob.bin", "content": "text now"})
    assert out.result is not None and out.result.ok
    assert (root / "blob.bin").read_text() == "text now"


async def test_run_survives_a_tool_call_with_bad_arguments(sandbox: Sandbox) -> None:
    provider = ScriptedProvider(
        [
            ModelResponse(tool_calls=[ToolCall("c1", "read_file", {})]),
            ModelResponse(text="recovered"),
        ]
    )
    runtime = assemble_runtime(
        config=_config(), sandbox=sandbox, mode=PermissionMode.PLAN, provider=provider
    )
    result = await runtime.run("read something")
    assert result.status is RunStatus.COMPLETE
    assert result.final_text == "recovered"
    # The model was told what went wrong so it could correct itself.
    assert "missing required argument" in provider.calls[1][-1].content
