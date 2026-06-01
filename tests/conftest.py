"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path_factory, monkeypatch):
    """Isolate every test from the real per-user global config.

    The config loader falls back to ~/.config/jarvis/config.toml (and
    ~/.jarvis/config.toml). Point HOME and XDG_CONFIG_HOME at an empty temp dir
    so tests that assume "no config present" don't pick up the developer's real
    global config. Tests that need a global config set XDG_CONFIG_HOME themselves
    (their monkeypatch runs after this fixture and wins).
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    yield
