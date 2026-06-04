"""Phase 1 sandbox enforcement tests.

Covers the required matrix: in-root passes; ``..`` traversal rejected; absolute
out-of-root rejected; symlink-to-outside rejected; inside-then-symlink-out
rejected; cwd validation rejects out-of-root dirs. Plus the safe primitives and
that violations are logged.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from jarvis_code.sandbox import (
    Sandbox,
    SandboxRootError,
    SandboxViolation,
    resolve_sandbox_root,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    d = tmp_path / "root"
    d.mkdir()
    (d / "sub").mkdir()
    (d / "file.txt").write_text("hello")
    return d


@pytest.fixture
def sandbox(root: Path) -> Sandbox:
    return Sandbox(root)


# -- resolve_sandbox_root ---------------------------------------------------


def test_resolve_root_defaults_to_launch_dir(root: Path) -> None:
    assert resolve_sandbox_root(root) == root.resolve()


def test_resolve_root_explicit_relative_is_relative_to_launch(root: Path) -> None:
    assert resolve_sandbox_root(root, "sub") == (root / "sub").resolve()


def test_resolve_root_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(SandboxRootError):
        resolve_sandbox_root(tmp_path / "does-not-exist")


def test_resolve_root_file_not_dir_raises(root: Path) -> None:
    with pytest.raises(SandboxRootError):
        resolve_sandbox_root(root / "file.txt")


# -- safe_path: the allow cases ---------------------------------------------


def test_in_root_relative_path_passes(sandbox: Sandbox, root: Path) -> None:
    assert sandbox.safe_path("file.txt") == (root / "file.txt").resolve()


def test_in_root_absolute_path_passes(sandbox: Sandbox, root: Path) -> None:
    assert sandbox.safe_path(root / "sub") == (root / "sub").resolve()


def test_nonexistent_in_root_path_ok_without_must_exist(sandbox: Sandbox, root: Path) -> None:
    p = sandbox.safe_path("new/deep/file.txt")
    assert p == (root / "new" / "deep" / "file.txt").resolve()


# -- safe_path: the deny cases ----------------------------------------------


def test_dotdot_traversal_rejected(sandbox: Sandbox) -> None:
    with pytest.raises(SandboxViolation):
        sandbox.safe_path("../outside.txt")


def test_deep_dotdot_traversal_rejected(sandbox: Sandbox) -> None:
    with pytest.raises(SandboxViolation):
        sandbox.safe_path("sub/../../escape")


def test_absolute_out_of_root_rejected(sandbox: Sandbox) -> None:
    with pytest.raises(SandboxViolation):
        sandbox.safe_path("/etc/passwd")


def test_must_exist_missing_rejected(sandbox: Sandbox) -> None:
    with pytest.raises(SandboxViolation):
        sandbox.safe_path("nope.txt", must_exist=True)


def test_symlink_to_outside_rejected(sandbox: Sandbox, root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret")
    link = root / "link"
    link.symlink_to(outside)
    # Accessing the symlink's target escapes the root → rejected.
    with pytest.raises(SandboxViolation):
        sandbox.safe_path("link/secret.txt")
    with pytest.raises(SandboxViolation):
        sandbox.read("link/secret.txt")


def test_inside_then_symlink_out_rejected(sandbox: Sandbox, root: Path, tmp_path: Path) -> None:
    # A real in-root directory that *contains* a symlink pointing outside.
    (root / "sub" / "inner").mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "data").write_text("x")
    (root / "sub" / "inner" / "out").symlink_to(outside)
    with pytest.raises(SandboxViolation):
        sandbox.safe_path("sub/inner/out/data")


def test_symlink_root_is_canonicalized(tmp_path: Path) -> None:
    real = tmp_path / "real_root"
    real.mkdir()
    (real / "f.txt").write_text("ok")
    link_root = tmp_path / "link_root"
    link_root.symlink_to(real)
    sb = Sandbox(link_root)
    assert sb.root == real.resolve()
    assert sb.read("f.txt") == "ok"


# -- safe primitives --------------------------------------------------------


def test_read_write_roundtrip(sandbox: Sandbox) -> None:
    sandbox.write("notes/todo.md", "content")
    assert sandbox.read("notes/todo.md") == "content"


def test_list_returns_in_root_children(sandbox: Sandbox, root: Path) -> None:
    names = {p.name for p in sandbox.list(".")}
    assert {"sub", "file.txt"} <= names


def test_list_skips_out_of_root_symlink(sandbox: Sandbox, root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "out"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    listed = {p.name for p in sandbox.list(".")}
    assert "escape" not in listed


def test_mkdir_and_delete(sandbox: Sandbox, root: Path) -> None:
    sandbox.mkdir("a/b/c")
    assert (root / "a" / "b" / "c").is_dir()
    sandbox.delete("a")
    assert not (root / "a").exists()


def test_delete_root_refused(sandbox: Sandbox) -> None:
    with pytest.raises(SandboxViolation):
        sandbox.delete(".")


def test_delete_symlink_does_not_follow(sandbox: Sandbox, root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "keep"
    outside.mkdir()
    (outside / "important").write_text("do not delete")
    (root / "link").symlink_to(outside)
    sandbox.delete("link")
    assert not (root / "link").exists()
    assert (outside / "important").exists()  # target untouched


def test_move_within_root(sandbox: Sandbox, root: Path) -> None:
    sandbox.move("file.txt", "sub/moved.txt")
    assert not (root / "file.txt").exists()
    assert (root / "sub" / "moved.txt").read_text() == "hello"


def test_move_out_of_root_rejected(sandbox: Sandbox) -> None:
    with pytest.raises(SandboxViolation):
        sandbox.move("file.txt", "../stolen.txt")


# -- regression: a trailing ".." leaf must not fool containment -------------
# Previously the follow_symlinks=False path (used by delete/move) re-attached a
# final ".." without normalizing it, so _is_within accepted "<root>/.." (the
# directory ABOVE the root) and "<root>/sub/.." (the root itself) — escaping
# containment and bypassing the "refuse to delete root" guard.


def test_dotdot_leaf_nofollow_rejected(sandbox: Sandbox) -> None:
    with pytest.raises(SandboxViolation):
        sandbox.safe_path("..", follow_symlinks=False)


def test_subdir_dotdot_leaf_nofollow_normalizes_to_root(sandbox: Sandbox, root: Path) -> None:
    resolved = sandbox.safe_path("sub/..", must_exist=True, follow_symlinks=False)
    assert resolved == root.resolve()
    assert ".." not in resolved.parts  # no surviving parent-escape component


def test_delete_dotdot_leaf_cannot_escape(sandbox: Sandbox, tmp_path: Path) -> None:
    sentinel = tmp_path / "DO_NOT_TOUCH"  # lives in the parent of the root
    sentinel.mkdir()
    with pytest.raises(SandboxViolation):
        sandbox.delete("..")
    assert sentinel.exists()


def test_delete_subdir_dotdot_refuses_root_wipe(sandbox: Sandbox, root: Path) -> None:
    with pytest.raises(SandboxViolation):
        sandbox.delete("sub/..")
    assert (root / "sub").exists() and (root / "file.txt").exists()


def test_move_dotdot_dst_cannot_escape(sandbox: Sandbox, root: Path, tmp_path: Path) -> None:
    with pytest.raises(SandboxViolation):
        sandbox.move("file.txt", "..")
    assert (root / "file.txt").exists()          # stayed in root
    assert not (tmp_path / "file.txt").exists()  # did not land in parent-of-root


# -- cwd validation ---------------------------------------------------------


def test_validate_cwd_none_is_root(sandbox: Sandbox, root: Path) -> None:
    assert sandbox.validate_cwd(None) == root.resolve()


def test_validate_cwd_subdir_ok(sandbox: Sandbox, root: Path) -> None:
    assert sandbox.validate_cwd("sub") == (root / "sub").resolve()


def test_validate_cwd_out_of_root_rejected(sandbox: Sandbox) -> None:
    with pytest.raises(SandboxViolation):
        sandbox.validate_cwd("..")


def test_validate_cwd_absolute_out_of_root_rejected(sandbox: Sandbox) -> None:
    with pytest.raises(SandboxViolation):
        sandbox.validate_cwd("/tmp")


def test_validate_cwd_file_rejected(sandbox: Sandbox) -> None:
    with pytest.raises(SandboxViolation):
        sandbox.validate_cwd("file.txt")


# -- logging ----------------------------------------------------------------


def test_violation_is_logged(sandbox: Sandbox, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="jarvis_code.sandbox"):
        with pytest.raises(SandboxViolation):
            sandbox.safe_path("../escape")
    assert any("sandbox violation" in r.message for r in caplog.records)


# -- env / user expansion ---------------------------------------------------


def test_env_var_expansion_still_validated(sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVIL", "/etc")
    with pytest.raises(SandboxViolation):
        sandbox.safe_path("$EVIL/passwd")


def test_user_expansion_outside_rejected(sandbox: Sandbox) -> None:
    # ~ expands to the real home, which is outside a tmp sandbox root.
    if os.path.expanduser("~") == str(sandbox.root):  # pragma: no cover
        pytest.skip("home happens to be the sandbox root")
    with pytest.raises(SandboxViolation):
        sandbox.safe_path("~/secrets")
