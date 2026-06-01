"""Phase 3 permission tests: the mode matrix, shell gating, and the 3-choice menu."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_code.permissions import (
    AutoApprovePrompter,
    Capability,
    Choice,
    Decision,
    DenyPrompter,
    OutcomeKind,
    PermissionManager,
    PermissionMode,
    PermissionRequest,
    PermissionResponse,
    ScriptedPrompter,
    TerminalPrompter,
    decide,
)
from jarvis_code.sandbox import Sandbox
from jarvis_code.tools import ToolContext, ToolExecutor, build_default_registry

READ_CAPS = [Capability.READ, Capability.SEARCH, Capability.GIT_READ]
MUTATE_CAPS = [Capability.WRITE, Capability.EDIT, Capability.DELETE, Capability.MOVE]


# -- policy matrix ----------------------------------------------------------


@pytest.mark.parametrize("cap", READ_CAPS)
def test_reads_allowed_in_every_mode(cap: Capability) -> None:
    for mode in PermissionMode:
        assert decide(mode, cap) == Decision.ALLOW


@pytest.mark.parametrize("cap", MUTATE_CAPS + [Capability.SHELL])
def test_plan_denies_mutations_and_shell(cap: Capability) -> None:
    assert decide(PermissionMode.PLAN, cap) == Decision.DENY


@pytest.mark.parametrize("cap", MUTATE_CAPS + [Capability.SHELL])
def test_auto_asks_for_mutations_and_shell(cap: Capability) -> None:
    assert decide(PermissionMode.AUTO, cap) == Decision.ASK


@pytest.mark.parametrize("cap", list(Capability))
def test_dangerous_allows_everything(cap: Capability) -> None:
    assert decide(PermissionMode.DANGEROUS, cap) == Decision.ALLOW


def test_subagent_allowed_in_all_modes() -> None:
    for mode in PermissionMode:
        assert decide(mode, Capability.SUBAGENT) == Decision.ALLOW


# -- manager authorize() ----------------------------------------------------


def _req(cap: Capability, name: str = "tool") -> PermissionRequest:
    return PermissionRequest(tool_name=name, capability=cap, summary="x")


def test_plan_write_denied() -> None:
    mgr = PermissionManager(PermissionMode.PLAN)
    out = mgr.authorize(_req(Capability.WRITE, "write_file"))
    assert out.kind == OutcomeKind.DENIED


def test_auto_shell_without_prompter_fails_closed() -> None:
    mgr = PermissionManager(PermissionMode.AUTO, prompter=None)
    out = mgr.authorize(_req(Capability.SHELL, "run_shell"))
    assert out.kind == OutcomeKind.DENIED  # never silently allow


def test_auto_shell_yes() -> None:
    mgr = PermissionManager(PermissionMode.AUTO, prompter=AutoApprovePrompter())
    out = mgr.authorize(_req(Capability.SHELL, "run_shell"))
    assert out.kind == OutcomeKind.ALLOWED


def test_auto_shell_no() -> None:
    mgr = PermissionManager(PermissionMode.AUTO, prompter=DenyPrompter())
    out = mgr.authorize(_req(Capability.SHELL, "run_shell"))
    assert out.kind == OutcomeKind.DENIED


def test_auto_do_something_else_returns_feedback() -> None:
    prompter = ScriptedPrompter(
        [PermissionResponse(choice=Choice.SOMETHING_ELSE, feedback="run the linter instead")]
    )
    mgr = PermissionManager(PermissionMode.AUTO, prompter=prompter)
    out = mgr.authorize(_req(Capability.SHELL, "run_shell"))
    assert out.kind == OutcomeKind.REVISE
    assert out.feedback == "run the linter instead"


def test_dangerous_never_calls_prompter() -> None:
    class ExplodingPrompter:
        def request_permission(self, request: PermissionRequest) -> PermissionResponse:
            raise AssertionError("dangerous mode must not prompt")

    mgr = PermissionManager(PermissionMode.DANGEROUS, prompter=ExplodingPrompter())
    assert mgr.authorize(_req(Capability.SHELL)).kind == OutcomeKind.ALLOWED


# -- the 3-choice terminal menu ---------------------------------------------


def test_terminal_menu_choice_yes() -> None:
    answers = iter(["1"])
    outputs: list[str] = []
    prompter = TerminalPrompter(input_fn=lambda _p: next(answers), output_fn=outputs.append)
    resp = prompter.request_permission(_req(Capability.SHELL, "run_shell"))
    assert resp.choice == Choice.YES
    rendered = "\n".join(outputs)
    assert "1) Yes" in rendered and "2) No" in rendered and "3) Do Something Else" in rendered


def test_terminal_menu_choice_no() -> None:
    answers = iter(["2"])
    prompter = TerminalPrompter(input_fn=lambda _p: next(answers), output_fn=lambda _s: None)
    assert prompter.request_permission(_req(Capability.SHELL)).choice == Choice.NO


def test_terminal_menu_do_something_else_collects_feedback() -> None:
    answers = iter(["3", "please run the tests instead"])
    prompter = TerminalPrompter(input_fn=lambda _p: next(answers), output_fn=lambda _s: None)
    resp = prompter.request_permission(_req(Capability.SHELL))
    assert resp.choice == Choice.SOMETHING_ELSE
    assert resp.feedback == "please run the tests instead"


def test_terminal_menu_reprompts_on_invalid() -> None:
    answers = iter(["7", "x", "1"])
    outputs: list[str] = []
    prompter = TerminalPrompter(input_fn=lambda _p: next(answers), output_fn=outputs.append)
    assert prompter.request_permission(_req(Capability.SHELL)).choice == Choice.YES
    assert any("1, 2, or 3" in o for o in outputs)


def test_injected_input_uses_typed_path_not_arrow_menu() -> None:
    # An injected input_fn (tests / pipes) must NOT enter raw-mode arrow handling.
    prompter = TerminalPrompter(input_fn=lambda _p: "1", output_fn=lambda _s: None)
    assert prompter._interactive is False
    # An explicit interactive=False also forces the typed path.
    assert TerminalPrompter(interactive=False)._interactive is False


# -- executor: end-to-end gating per mode -----------------------------------


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    (tmp_path / "a.txt").write_text("hello")
    return Sandbox(tmp_path)


def _executor(sandbox: Sandbox, mode: PermissionMode, prompter=None) -> ToolExecutor:
    return ToolExecutor(
        build_default_registry(),
        PermissionManager(mode, prompter=prompter),
        ToolContext(sandbox=sandbox),
    )


def test_plan_mode_blocks_write_but_allows_read(sandbox: Sandbox) -> None:
    ex = _executor(sandbox, PermissionMode.PLAN)
    read = ex.execute("read_file", {"path": "a.txt"})
    assert read.outcome == OutcomeKind.ALLOWED and read.result.ok

    write = ex.execute("write_file", {"path": "b.txt", "content": "x"})
    assert write.outcome == OutcomeKind.DENIED
    assert not (sandbox.root / "b.txt").exists()  # nothing was written


def test_auto_mode_write_approved_writes_file(sandbox: Sandbox) -> None:
    ex = _executor(sandbox, PermissionMode.AUTO, prompter=AutoApprovePrompter())
    res = ex.execute("write_file", {"path": "b.txt", "content": "new"})
    assert res.outcome == OutcomeKind.ALLOWED and res.result.ok
    assert (sandbox.root / "b.txt").read_text() == "new"


def test_auto_mode_write_declined_leaves_disk_untouched(sandbox: Sandbox) -> None:
    ex = _executor(sandbox, PermissionMode.AUTO, prompter=DenyPrompter())
    res = ex.execute("write_file", {"path": "b.txt", "content": "new"})
    assert res.outcome == OutcomeKind.DENIED
    assert not (sandbox.root / "b.txt").exists()


def test_auto_shell_gated_and_runs_on_yes(sandbox: Sandbox) -> None:
    ex = _executor(sandbox, PermissionMode.AUTO, prompter=AutoApprovePrompter())
    res = ex.execute("run_shell", {"command": "echo hi"})
    assert res.outcome == OutcomeKind.ALLOWED
    assert res.result.ok and "hi" in res.result.output


def test_auto_shell_request_includes_command_cwd_risk(sandbox: Sandbox) -> None:
    prompter = ScriptedPrompter([PermissionResponse(choice=Choice.NO)])
    ex = _executor(sandbox, PermissionMode.AUTO, prompter=prompter)
    ex.execute("run_shell", {"command": "rm -rf build"}, rationale="clean build dir")
    req = prompter.requests[0]
    assert req.command == "rm -rf build"
    assert req.cwd == "."
    assert req.why == "clean build dir"
    assert req.risk.value in ("medium", "high")


def test_executor_revise_propagates_feedback(sandbox: Sandbox) -> None:
    prompter = ScriptedPrompter(
        [PermissionResponse(choice=Choice.SOMETHING_ELSE, feedback="use ruff")]
    )
    ex = _executor(sandbox, PermissionMode.AUTO, prompter=prompter)
    res = ex.execute("run_shell", {"command": "flake8 ."})
    assert res.outcome == OutcomeKind.REVISE
    assert res.feedback == "use ruff"
    assert not res.ran
