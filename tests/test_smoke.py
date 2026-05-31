"""Phase 0 smoke tests: the package imports and the CLI is wired up."""

from __future__ import annotations

from typer.testing import CliRunner

import jarvis_code
from jarvis_code.cli.main import app

runner = CliRunner()


def test_package_has_version() -> None:
    assert isinstance(jarvis_code.__version__, str)
    assert jarvis_code.__version__


def test_help_runs() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "jarvis" in result.output.lower()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "jarvis-code" in result.output
