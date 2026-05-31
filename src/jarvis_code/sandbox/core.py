"""The deterministic filesystem chokepoint.

Every filesystem access in jarvis-code goes through a :class:`Sandbox`. The
contract:

* All input paths are treated as untrusted (they often come from the model).
* Each path is expanded (``~`` and ``$VAR``), made absolute against the root,
  and canonicalized with **symlinks resolved** *before* any use.
* The canonical result is re-validated to be inside the resolved root.
  Anything outside — via ``..``, an absolute path, or a symlink whose target
  escapes — raises :class:`SandboxViolation`, which is logged.
* Subprocess working directories are validated the same way.

No other layer may touch the filesystem except through this module.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from jarvis_code.utils.logging import get_logger

from .errors import SandboxRootError, SandboxViolation

_log = get_logger("sandbox")


def _expand(raw: str) -> str:
    """Apply env-var (``$VAR``/``${VAR}``) then user (``~``) expansion."""
    return os.path.expanduser(os.path.expandvars(raw))


def resolve_sandbox_root(
    launch_dir: str | os.PathLike[str],
    explicit_path: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve the sandbox root.

    The root is ``explicit_path`` if given (a relative explicit path is taken
    relative to ``launch_dir``), otherwise ``launch_dir``. The result is
    expanded, canonicalized (symlinks resolved), and required to be an existing
    directory.
    """
    launch = Path(_expand(os.fspath(launch_dir)))

    if explicit_path is not None:
        candidate = Path(_expand(os.fspath(explicit_path)))
        if not candidate.is_absolute():
            candidate = launch / candidate
    else:
        candidate = launch

    root = candidate.resolve()

    if not root.exists():
        raise SandboxRootError(f"Sandbox root does not exist: {root}")
    if not root.is_dir():
        raise SandboxRootError(f"Sandbox root is not a directory: {root}")
    return root


class Sandbox:
    """Safe, root-confined filesystem primitives.

    Construct with a resolved root (see :func:`resolve_sandbox_root`). Every
    method validates its path arguments via :meth:`safe_path` first.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        root_path = Path(_expand(os.fspath(root))).resolve()
        if not root_path.is_dir():
            raise SandboxRootError(f"Sandbox root is not a directory: {root_path}")
        self._root = root_path

    @property
    def root(self) -> Path:
        return self._root

    # -- core validation ---------------------------------------------------

    def _is_within(self, resolved: Path) -> bool:
        return resolved == self._root or self._root in resolved.parents

    def safe_path(
        self,
        candidate: str | os.PathLike[str],
        *,
        must_exist: bool = False,
        follow_symlinks: bool = True,
    ) -> Path:
        """Canonicalize ``candidate`` and guarantee it is inside the root.

        Returns the resolved absolute path. Raises :class:`SandboxViolation`
        (logged) if the canonical path is outside the root, or if symlinks in
        the path resolve outside it. With ``must_exist=True`` a missing path
        also raises ``SandboxViolation``.

        With ``follow_symlinks=False`` the *parent* chain is still fully
        canonicalized (so a symlinked parent escaping the root is rejected), but
        a final-component symlink is **not** dereferenced. This is the correct
        semantics for operations that act on the link entry itself — deleting or
        moving a symlink that lives inside the root removes/moves the link, never
        its target. Reads/writes always follow symlinks and so cannot escape.
        """
        raw = os.fspath(candidate)
        expanded = _expand(raw)
        p = Path(expanded)
        if not p.is_absolute():
            p = self._root / p

        if follow_symlinks:
            # resolve(strict=False) follows symlinks in the existing portion of
            # the path and normalizes ``..`` — this catches symlink escapes and
            # traversal, including for not-yet-existing leaf components.
            resolved = p.resolve()
            exists = resolved.exists()
        else:
            # Canonicalize the parent (still follows a symlinked parent out of
            # root), then re-attach the un-dereferenced leaf name.
            resolved = p.parent.resolve() / p.name
            exists = os.path.lexists(resolved)

        if not self._is_within(resolved):
            self._violation(
                "Path resolves outside the sandbox root",
                attempted=raw,
                resolved=str(resolved),
            )

        if must_exist and not exists:
            self._violation(
                "Path does not exist",
                attempted=raw,
                resolved=str(resolved),
            )
        return resolved

    def _violation(self, message: str, *, attempted: str, resolved: str) -> None:
        _log.warning(
            "sandbox violation: %s | attempted=%r resolved=%r root=%r",
            message,
            attempted,
            resolved,
            str(self._root),
        )
        raise SandboxViolation(
            f"{message}: {attempted!r}",
            attempted=attempted,
            resolved=resolved,
            root=str(self._root),
        )

    def relpath(self, candidate: str | os.PathLike[str]) -> str:
        """Return a display path relative to the root (validated first)."""
        resolved = self.safe_path(candidate)
        if resolved == self._root:
            return "."
        return str(resolved.relative_to(self._root))

    # -- read / write ------------------------------------------------------

    def read(
        self,
        path: str | os.PathLike[str],
        *,
        encoding: str = "utf-8",
    ) -> str:
        resolved = self.safe_path(path, must_exist=True)
        if not resolved.is_file():
            raise SandboxViolation(
                f"Not a regular file: {path!r}",
                attempted=os.fspath(path),
                resolved=str(resolved),
                root=str(self._root),
            )
        return resolved.read_text(encoding=encoding)

    def read_bytes(self, path: str | os.PathLike[str]) -> bytes:
        resolved = self.safe_path(path, must_exist=True)
        return resolved.read_bytes()

    def append(
        self,
        path: str | os.PathLike[str],
        content: str,
        *,
        encoding: str = "utf-8",
        create_parents: bool = True,
    ) -> Path:
        """Append text to an in-root file (used for append-only JSONL logs)."""
        resolved = self.safe_path(path)
        if create_parents:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        with resolved.open("a", encoding=encoding) as fh:
            fh.write(content)
        return resolved

    def write(
        self,
        path: str | os.PathLike[str],
        content: str,
        *,
        encoding: str = "utf-8",
        create_parents: bool = True,
    ) -> Path:
        resolved = self.safe_path(path)
        if create_parents:
            # Parents of an in-root path are themselves in-root (down to root),
            # so creating them cannot escape.
            resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding=encoding)
        return resolved

    def write_bytes(
        self,
        path: str | os.PathLike[str],
        content: bytes,
        *,
        create_parents: bool = True,
    ) -> Path:
        resolved = self.safe_path(path)
        if create_parents:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(content)
        return resolved

    # -- listing -----------------------------------------------------------

    def list(self, path: str | os.PathLike[str] = ".") -> list[Path]:
        """Return sorted child paths of a directory (each validated in-root)."""
        resolved = self.safe_path(path, must_exist=True)
        if not resolved.is_dir():
            raise SandboxViolation(
                f"Not a directory: {path!r}",
                attempted=os.fspath(path),
                resolved=str(resolved),
                root=str(self._root),
            )
        children: list[Path] = []
        for child in resolved.iterdir():
            # Re-validate: an entry could itself be a symlink pointing out.
            if self._is_within(child.resolve()):
                children.append(child)
            else:
                _log.warning(
                    "sandbox: skipping out-of-root entry %r under %r",
                    str(child),
                    str(resolved),
                )
        return sorted(children)

    # -- mutation ----------------------------------------------------------

    def mkdir(
        self,
        path: str | os.PathLike[str],
        *,
        parents: bool = True,
        exist_ok: bool = True,
    ) -> Path:
        resolved = self.safe_path(path)
        resolved.mkdir(parents=parents, exist_ok=exist_ok)
        return resolved

    def delete(self, path: str | os.PathLike[str]) -> None:
        # follow_symlinks=False: a symlink living in the root is removed as a
        # link, never followed to delete whatever it points at.
        resolved = self.safe_path(path, must_exist=True, follow_symlinks=False)
        if resolved == self._root:
            raise SandboxViolation(
                "Refusing to delete the sandbox root",
                attempted=os.fspath(path),
                resolved=str(resolved),
                root=str(self._root),
            )
        # is_symlink() is checked before is_dir(): for a symlink we remove the
        # link itself, never recursing through it to whatever it points at.
        if resolved.is_symlink() or resolved.is_file():
            resolved.unlink()
        elif resolved.is_dir():
            shutil.rmtree(resolved)
        else:  # pragma: no cover - exotic file types
            resolved.unlink()

    def move(
        self,
        src: str | os.PathLike[str],
        dst: str | os.PathLike[str],
    ) -> Path:
        resolved_src = self.safe_path(src, must_exist=True, follow_symlinks=False)
        resolved_dst = self.safe_path(dst, follow_symlinks=False)
        if resolved_src == self._root:
            raise SandboxViolation(
                "Refusing to move the sandbox root",
                attempted=os.fspath(src),
                resolved=str(resolved_src),
                root=str(self._root),
            )
        resolved_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(os.fspath(resolved_src), os.fspath(resolved_dst))
        return resolved_dst

    # -- subprocess working directory --------------------------------------

    def validate_cwd(self, path: str | os.PathLike[str] | None = None) -> Path:
        """Validate a subprocess working directory.

        ``None`` means "use the sandbox root". Any other value must resolve to
        an existing directory inside the root.
        """
        if path is None:
            return self._root
        resolved = self.safe_path(path, must_exist=True)
        if not resolved.is_dir():
            raise SandboxViolation(
                f"Working directory is not a directory: {path!r}",
                attempted=os.fspath(path),
                resolved=str(resolved),
                root=str(self._root),
            )
        return resolved

    # -- convenience -------------------------------------------------------

    def exists(self, path: str | os.PathLike[str]) -> bool:
        try:
            resolved = self.safe_path(path)
        except SandboxViolation:
            return False
        return resolved.exists()

    def iter_files(
        self,
        path: str | os.PathLike[str] = ".",
    ) -> Iterable[Path]:
        """Yield every in-root regular file under ``path`` (recursive)."""
        resolved = self.safe_path(path, must_exist=True)
        for child in resolved.rglob("*"):
            try:
                if child.is_file() and self._is_within(child.resolve()):
                    yield child
            except OSError:  # pragma: no cover - broken symlink, races
                continue
