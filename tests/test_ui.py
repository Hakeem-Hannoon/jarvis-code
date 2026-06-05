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


def test_tool_started_shows_verb_and_path_as_tree() -> None:
    out, echo = _capture()
    r = ConsoleReporter(echo=echo)
    r.tool_started("read_file", "src/app.py")
    r.tool_started("write_file", "src/new.py")
    r.tool_started("edit_file", "src/loop.py")
    blob = _plain(out)
    assert "reading src/app.py" in blob
    assert "writing src/new.py" in blob
    assert "editing src/loop.py" in blob
    assert blob.count("├─") == 3  # tree-style branches, one per file


def test_tool_started_suppresses_run_shell() -> None:
    out, echo = _capture()
    ConsoleReporter(echo=echo).tool_started("run_shell", "pytest -q")
    assert out == []  # the boxed command is the display for shell, not a tree line
