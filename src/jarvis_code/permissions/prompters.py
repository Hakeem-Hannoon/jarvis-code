"""Prompter implementations.

The terminal prompter offers the three choices the instructions require —
**Approve / Deny / Do something else** — as an arrow-key menu in an interactive
terminal (↑/↓ to move, Enter to confirm; 1/2/3 still work as shortcuts). When
stdin isn't an interactive TTY (pipes, tests, injected input), it falls back to
the typed ``1/2/3`` prompt. "Do something else" collects free-text steering.

The non-interactive prompters are for ``dangerous`` mode (never asks) and tests.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from .manager import Choice, PermissionRequest, PermissionResponse


class AutoApprovePrompter:
    """Always approves. Used internally where policy already decided ALLOW."""

    def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        return PermissionResponse(choice=Choice.YES)


class DenyPrompter:
    """Always declines. Useful as a safe default and in tests."""

    def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        return PermissionResponse(choice=Choice.NO)


class ScriptedPrompter:
    """Returns pre-scripted responses in order (tests)."""

    def __init__(self, responses: list[PermissionResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[PermissionRequest] = []

    def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("ScriptedPrompter ran out of responses")
        return self._responses.pop(0)


def _request_body_lines(request: PermissionRequest) -> list[str]:
    """The human-facing approval block (no secrets), without the choices line."""
    lines = [f"Requesting permission: {request.summary}"]
    if request.command is not None:
        lines.append(f"  command:         {request.command}")
    if request.cwd is not None:
        lines.append(f"  working dir:     {request.cwd}")
    if request.why is not None:
        lines.append(f"  why:             {request.why}")
    if request.expected_effect is not None:
        lines.append(f"  expected effect: {request.expected_effect}")
    lines.append(f"  risk level:      {request.risk.value}")
    if request.diff:
        lines.append("  diff:")
        lines.extend(f"    {ln}" for ln in request.diff.splitlines())
    return lines


def render_request(request: PermissionRequest) -> str:
    """Render the approval block with the typed 1/2/3 choices line."""
    return "\n".join(_request_body_lines(request) + ["  1) Yes   2) No   3) Do Something Else"])


# Menu options in order, paired with the choice they produce.
_MENU: list[tuple[str, Choice]] = [
    ("Approve", Choice.YES),
    ("Deny", Choice.NO),
    ("Do something else", Choice.SOMETHING_ELSE),
]


def _stdin_is_interactive(input_fn: Callable[[str], str]) -> bool:
    # Only drive the raw-mode menu for the real builtin input on a TTY; an
    # injected input_fn (tests) or a pipe means use the typed fallback.
    if input_fn is not input:
        return False
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:  # pragma: no cover - unusual stdio
        return False


def _read_key(fd: int) -> str:
    """Read one logical keypress in cbreak mode: up/down/enter/digits/etc."""
    import select

    ch = os.read(fd, 1)
    if ch in (b"\r", b"\n"):
        return "enter"
    if ch == b"\x03":  # Ctrl-C
        return "interrupt"
    if ch == b"\x1b":  # escape — maybe an arrow sequence
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            return "escape"
        seq = os.read(fd, 2).decode("ascii", "ignore")
        return {"[A": "up", "[B": "down"}.get(seq, "other")
    return ch.decode("utf-8", "ignore")


class TerminalPrompter:
    """Interactive approval menu (arrow keys) with a typed 1/2/3 fallback."""

    def __init__(
        self,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        *,
        interactive: bool | None = None,
    ) -> None:
        self._input = input_fn
        self._output = output_fn
        self._interactive = (
            _stdin_is_interactive(input_fn) if interactive is None else interactive
        )

    def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        if self._interactive:
            resp = self._arrow_menu(request)
            if resp is not None:
                return resp  # else the terminal couldn't do raw mode -> typed
        return self._typed_menu(request)

    # -- typed fallback (also what tests exercise) -------------------------

    def _typed_menu(self, request: PermissionRequest) -> PermissionResponse:
        self._output(render_request(request))
        while True:
            raw = self._input("Choose [1/2/3]: ").strip().lower()
            if raw in ("1", "y", "yes"):
                return PermissionResponse(choice=Choice.YES)
            if raw in ("2", "n", "no"):
                return PermissionResponse(choice=Choice.NO)
            if raw in ("3", "e", "else"):
                feedback = self._input("What should I do instead? ").strip()
                return PermissionResponse(choice=Choice.SOMETHING_ELSE, feedback=feedback or None)
            self._output("Please choose 1, 2, or 3.")

    # -- interactive arrow-key menu ----------------------------------------

    def _arrow_menu(self, request: PermissionRequest) -> PermissionResponse | None:
        try:
            import termios
            import tty
        except Exception:  # pragma: no cover - non-POSIX (Windows): use typed
            return None
        try:
            fd = sys.stdin.fileno()
        except (ValueError, OSError):  # pragma: no cover - no real stdin
            return None

        out = sys.stdout
        self._output("\n".join(_request_body_lines(request)))
        self._output("  Use ↑/↓ (or 1/2/3), Enter to confirm:")

        idx = 0

        def draw(first: bool) -> None:
            if not first:
                out.write(f"\x1b[{len(_MENU)}A")  # cursor up to the first option
            for i, (label, _c) in enumerate(_MENU):
                selected = i == idx
                pointer = "❯" if selected else " "
                line = f"  {pointer} {label}"
                if selected:
                    line = f"\x1b[1;36m{line}\x1b[0m"  # cyan bold
                out.write("\x1b[2K" + line + "\n")  # clear line, then text
            out.flush()

        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            out.write("\x1b[?25l")  # hide cursor
            draw(first=True)
            while True:
                key = _read_key(fd)
                if key == "up":
                    idx = (idx - 1) % len(_MENU)
                elif key == "down":
                    idx = (idx + 1) % len(_MENU)
                elif key in ("1", "2", "3"):
                    idx = int(key) - 1
                    break
                elif key == "enter":
                    break
                elif key in ("escape", "interrupt"):
                    idx = 1  # cancel == Deny (the safe choice)
                    break
                else:
                    continue
                draw(first=False)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            out.write("\x1b[?25h")  # restore cursor
            out.flush()

        label, choice = _MENU[idx]
        out.write(f"  → {label}\n")
        out.flush()
        if choice == Choice.SOMETHING_ELSE:
            feedback = self._input("What should I do instead? ").strip()
            return PermissionResponse(choice=choice, feedback=feedback or None)
        return PermissionResponse(choice=choice)
