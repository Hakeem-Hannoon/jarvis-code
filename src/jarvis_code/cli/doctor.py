"""`jarvis doctor` — environment & config health checks.

Prints pass/fail for each check. NEVER prints a key value — only whether a key
is present (a boolean). Works before `jarvis init` (config is optional here).
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import typer

from jarvis_code import __version__
from jarvis_code.config import (
    ConfigError,
    SttEngineName,
    VoiceEngineName,
    env_var_for,
    find_config_path,
    load_config,
)
from jarvis_code.sandbox import SandboxRootError, resolve_sandbox_root

from .options import GlobalOptions


@dataclass
class Check:
    label: str
    status: bool | None  # True=pass, False=fail, None=informational/skipped
    detail: str


def _have(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


def _short(path: Path) -> str:
    """Abbreviate the home directory to ``~`` so rows stay on one line."""
    try:
        rel = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    return "~" if str(rel) == "." else f"~/{rel.as_posix()}"


def collect_checks(opts: GlobalOptions) -> list[Check]:
    checks: list[Check] = []

    checks.append(
        Check("Python >= 3.11", sys.version_info >= (3, 11), platform.python_version())
    )
    checks.append(Check("jarvis_code installed", True, f"v{__version__}"))
    on_path = shutil.which("jarvis")
    checks.append(Check("jarvis on PATH", on_path is not None, on_path or "not found"))

    root: Path | None = None
    try:
        root = resolve_sandbox_root(Path.cwd(), opts.sandbox)
        checks.append(Check("sandbox root", True, _short(root)))
    except SandboxRootError as exc:
        checks.append(Check("sandbox root", False, str(exc)))

    base = root or Path.cwd()
    cfg_path = find_config_path(base)
    checks.append(
        Check("config present", cfg_path is not None, _short(cfg_path) if cfg_path else "run: jarvis init")
    )

    config = None
    if cfg_path is not None:
        try:
            config = load_config(cfg_path)
            checks.append(Check("config valid", True, "parsed OK"))
        except ConfigError as exc:
            checks.append(Check("config valid", False, str(exc).splitlines()[0]))

    if config is not None:
        name = opts.provider or config.default_provider
        checks.append(Check("selected provider", name in config.providers, name))
        pconf = config.providers.get(name)
        has_key = bool(pconf and pconf.has_key())
        # Boolean only — never the key value.
        checks.append(
            Check(
                f"API key for '{name}'",
                has_key,
                "present" if has_key else f"missing (set {env_var_for(name)})",
            )
        )

        voice = config.voice
        from jarvis_code.voice import select_engine

        selected = select_engine(voice)
        checks.append(
            Check(
                "voice output engine",
                True,
                f"configured={voice.engine.value}, would use={selected.name}",
            )
        )
        # Voice is optional: a missing engine is informational ("–") unless voice
        # output is switched on, in which case it is the reason you hear nothing.
        missing = False if voice.enabled else None
        if voice.engine == VoiceEngineName.PIPER:
            have_piper = _have("piper")
            checks.append(
                Check("Piper TTS", True if have_piper else missing,
                      "ok" if have_piper else "not installed — pip install '.[voice]'")
            )
        from jarvis_code.voice import system_tts_available

        if sys.platform == "darwin":
            sys_detail = "ok — using macOS 'say'" if shutil.which("say") else "say binary missing"
        else:
            sys_detail = "ok (pyttsx3)" if _have("pyttsx3") else "not installed — pip install '.[voice]'"
        have_system = system_tts_available()
        checks.append(Check("system TTS", True if have_system else missing, sys_detail))

        if voice.input.enabled:
            stt_mod = "faster_whisper" if voice.input.stt_engine == SttEngineName.FASTER_WHISPER else "vosk"
            checks.append(
                Check(
                    f"local STT ({voice.input.stt_engine.value})",
                    _have(stt_mod),
                    "ok" if _have(stt_mod) else "pip install '.[stt]'",
                )
            )
            checks.append(
                Check(
                    "microphone (sounddevice)",
                    _have("sounddevice"),
                    "ok — OS must also grant mic permission" if _have("sounddevice") else "pip install '.[stt]'",
                )
            )

    if shutil.which("git") and root is not None:
        is_repo = (root / ".git").exists()
        checks.append(Check("git repository", is_repo, "yes" if is_repo else "not a git repo"))

    return checks


def run_doctor(opts: GlobalOptions) -> int:
    checks = collect_checks(opts)
    width = max(len(c.label) for c in checks)
    typer.echo(typer.style("jarvis doctor", bold=True))
    typer.echo("")
    for check in checks:
        if check.status is True:
            mark = typer.style("✓", fg=typer.colors.GREEN)
            detail = typer.style(check.detail, dim=True)
        elif check.status is False:
            mark = typer.style("✗", fg=typer.colors.RED)
            detail = typer.style(check.detail, fg=typer.colors.YELLOW)
        else:
            mark = typer.style("–", fg=typer.colors.YELLOW)
            detail = typer.style(check.detail, dim=True)
        typer.echo(f"  {mark} {check.label:<{width}}  {detail}")

    failed = [c for c in checks if c.status is False]
    typer.echo("")
    if failed:
        noun = "check needs" if len(failed) == 1 else "checks need"
        typer.echo(typer.style(f"  {len(failed)} {noun} attention.", fg=typer.colors.YELLOW))
    else:
        typer.echo(typer.style("  All checks passed.", fg=typer.colors.GREEN))
    return 0
