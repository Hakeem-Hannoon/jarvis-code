"""Console rendering: risk-coloured command boxes and "updates" diffs."""

from __future__ import annotations

import re

from jarvis_code.agent.reporter import NoopReporter
from jarvis_code.cli.ui import ConsoleReporter, render_command_box, render_updates
from jarvis_code.permissions import Capability, PermissionRequest
from jarvis_code.permissions.manager import RiskLevel
from jarvis_code.utils.diffs import unified_diff

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _capture():
    out: list[str] = []
    return out, out.append


def _plain(lines: list[str]) -> str:
    return _ANSI.sub("", "\n".join(lines))


def test_command_box_shows_command_reason_and_risk() -> None:
    out, echo = _capture()
    render_command_box("pytest -q", "verify the change", RiskLevel.MEDIUM, echo)
    blob = _plain(out)
    assert "pytest -q" in blob          # the exact command
    assert "why: verify the change" in blob  # reasoning under it
    assert "risk: MEDIUM" in blob       # ranked risk label


def test_command_box_colour_tracks_risk() -> None:
    # Green for low, red for high — the ANSI colour differs by risk.
    low, echo_low = _capture()
    render_command_box("ls", None, RiskLevel.LOW, echo_low)
    high, echo_high = _capture()
    render_command_box("sudo rm x", None, RiskLevel.HIGH, echo_high)
    assert "\n".join(low) != "\n".join(high)  # different colouring
    assert "\x1b[" in "\n".join(low)          # actually coloured


def test_updates_shows_label_and_coloured_diff() -> None:
    out, echo = _capture()
    render_updates(unified_diff("alpha\n", "beta\n", "f.py"), echo)
    blob = _plain(out)
    assert "updates" in blob
    assert "-alpha" in blob  # deletion
    assert "+beta" in blob   # addition


def test_console_action_dispatches_shell_vs_edit() -> None:
    out, echo = _capture()
    r = ConsoleReporter(echo=echo)
    r.action(PermissionRequest(
        tool_name="run_shell", capability=Capability.SHELL, summary="x",
        command="ls -la", risk=RiskLevel.LOW,
    ))
    r.action(PermissionRequest(
        tool_name="edit_file", capability=Capability.EDIT, summary="x",
        diff=unified_diff("a\n", "b\n", "f"),
    ))
    blob = _plain(out)
    assert "ls -la" in blob and "risk: LOW" in blob  # shell -> box
    assert "updates" in blob                          # edit -> updates diff


def test_console_action_is_silent_for_a_plain_read() -> None:
    out, echo = _capture()
    ConsoleReporter(echo=echo).action(
        PermissionRequest(tool_name="read_file", capability=Capability.READ, summary="x")
    )
    assert out == []  # no command, no diff -> nothing rendered


def test_noop_reporter_action_is_callable() -> None:
    NoopReporter().action(object())  # must not raise


def test_each_tool_is_one_tree_line_with_its_result() -> None:
    # A tool's start and result print as ONE line when it finishes — no more
    # "✓ read_file (18 chars)" second line.
    out, echo = _capture()
    r = ConsoleReporter(echo=echo)
    r.tool_started("read_file", "src/app.py")
    r.tool_finished("read_file", True, "212 lines")
    r.tool_started("write_file", "src/new.py")
    r.tool_finished("write_file", True, "+12 -0 lines")
    r.tool_started("edit_file", "src/loop.py")
    r.tool_finished("edit_file", False, "old_string not found in file.")
    blob = _plain(out)
    assert "reading src/app.py  · 212 lines" in blob
    assert "writing src/new.py  · +12 -0 lines" in blob
    assert "editing src/loop.py  ✗ old_string not found" in blob
    assert blob.count("├─") == 3  # tree-style branches, one per file
    assert len(out) == 3  # and nothing else


def test_a_pending_tool_line_is_flushed_before_other_output() -> None:
    out, echo = _capture()
    r = ConsoleReporter(echo=echo)
    r.tool_started("edit_file", "f.py")
    r.action(PermissionRequest(
        tool_name="edit_file", capability=Capability.EDIT, summary="x",
        diff=unified_diff("a\n", "b\n", "f.py"),
    ))
    blob = _plain(out)
    assert blob.index("editing f.py") < blob.index("updates")  # start line first


def test_denied_tool_prints_one_line_not_two() -> None:
    out, echo = _capture()
    r = ConsoleReporter(echo=echo)
    r.tool_started("write_file", "x.py")
    r.permission("write_file", "denied", "plan mode does not allow 'write'")
    r.tool_finished("write_file", False, "permission denied: plan mode does not allow 'write'")
    blob = _plain(out)
    assert blob.count("denied") == 1


def test_shell_output_excerpt_is_shown_and_elided() -> None:
    out, echo = _capture()
    r = ConsoleReporter(echo=echo)
    r.tool_output("run_shell", "\n".join(f"line {i}" for i in range(100)), True)
    r.tool_finished("run_shell", True, "exit 0")
    blob = _plain(out)
    assert "line 0" in blob and "line 99" in blob  # head and tail
    assert "more lines" in blob and "line 50" not in blob  # middle elided
    assert "✓ exit 0" in blob


def test_model_text_cannot_inject_terminal_escapes() -> None:
    out, echo = _capture()
    r = ConsoleReporter(echo=echo)
    r.assistant_text("done\x1b[2J\x1b[H fake prompt")  # clear screen + home
    r.tool_started("read_file", "a\x1b[31mb")
    r.tool_finished("read_file", True, "1 line")
    raw = "\n".join(out)
    assert "\x1b[2J" not in raw and "\x1b[H" not in raw and "\x1b[31mb" not in raw
    assert "\\x1b[2J" in raw  # shown literally instead


def test_reply_is_not_repeated_as_the_command_reason() -> None:
    out, echo = _capture()
    r = ConsoleReporter(echo=echo)
    r.assistant_text("Running the tests.")
    r.action(PermissionRequest(
        tool_name="run_shell", capability=Capability.SHELL, summary="x",
        command="pytest -q", why="Running the tests.", risk=RiskLevel.LOW,
    ))
    assert _plain(out).count("Running the tests.") == 1


def test_turn_footer_explains_a_failed_turn() -> None:
    from jarvis_code.agent import RunResult, RunState, RunStatus
    from jarvis_code.cli.ui import print_turn_footer

    out, echo = _capture()
    result = RunResult(
        RunStatus.ERROR,
        "anthropic API error 401: invalid x-api-key\n  hint: the API key was rejected",
        1, RunState(), seconds=0.4,
    )
    print_turn_footer(result, echo)
    blob = _plain(out)
    assert "error — anthropic API error 401: invalid x-api-key" in blob  # not just "[error]"
    assert "hint: the API key was rejected" in blob


def test_turn_footer_shows_stats_and_this_turns_files() -> None:
    from jarvis_code.agent import RunResult, RunState, RunStatus
    from jarvis_code.cli.ui import print_turn_footer

    out, echo = _capture()
    result = RunResult(
        RunStatus.COMPLETE, "done", 3, RunState(files_changed=["old.py", "new.py"]),
        seconds=4.13, tokens_in=2400, tokens_out=310, files_changed=("new.py",),
    )
    print_turn_footer(result, echo)
    blob = _plain(out)
    assert "4.1s · 3 steps · 2.4k in / 310 out tokens" in blob
    assert "changed: new.py" in blob and "old.py" not in blob  # this turn only


def test_approval_panel_shows_everything_the_invariants_require() -> None:
    from jarvis_code.cli.approval import approval_lines

    blob = _plain(approval_lines(PermissionRequest(
        tool_name="run_shell", capability=Capability.SHELL, summary="Run a shell command.",
        command="pytest -q", cwd="src", why="verify the change",
        expected_effect="Executes the command; output is captured.", risk=RiskLevel.HIGH,
    )))
    for needle in ("pytest -q", "src", "verify the change", "Executes the command", "risk: HIGH"):
        assert needle in blob

    edit = _plain(approval_lines(PermissionRequest(
        tool_name="edit_file", capability=Capability.EDIT, summary="edit_file f.py",
        diff=unified_diff("alpha\n", "beta\n", "f.py"),
    )))
    assert "approve · edit f.py" in edit and "-alpha" in edit and "+beta" in edit


def test_tool_started_suppresses_run_shell() -> None:
    out, echo = _capture()
    ConsoleReporter(echo=echo).tool_started("run_shell", "pytest -q")
    assert out == []  # the boxed command is the display for shell, not a tree line
