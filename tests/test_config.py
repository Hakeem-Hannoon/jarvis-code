"""Phase 2 config + secrets tests.

Covers: valid load; env override beats file; missing-config and missing-key
friendly errors; and secret redaction (no key value appears in any log or
transcript-style output).
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from jarvis_code.config import (
    ConfigNotFoundError,
    JarvisConfig,
    MissingAPIKeyError,
    load_config,
    require_provider,
    validate_config_file,
)
from jarvis_code.utils import logging as jlog

FILE_KEY = "sk-file-AAAAAAAAAAAAAAAAAAAA"
ENV_KEY = "sk-env-BBBBBBBBBBBBBBBBBBBB"

VALID_TOML = """
default_provider = "anthropic"

[providers.anthropic]
api_key = "%s"
model = "claude-opus-4-8"

[providers.openai]
api_key = ""
model = "gpt-4o"

[voice]
enabled = false
engine = "piper"

[voice.input]
mode = "muted"
""" % FILE_KEY


@pytest.fixture(autouse=True)
def _clean_secrets():
    jlog.clear_secrets()
    yield
    jlog.clear_secrets()


def _write_config(base: Path, body: str = VALID_TOML) -> Path:
    cfg_dir = base / ".jarvis"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "config.toml"
    path.write_text(body)
    return path


# -- loading ----------------------------------------------------------------


def test_loads_valid_config(tmp_path: Path) -> None:
    _write_config(tmp_path)
    config = load_config(base_dir=tmp_path, env={})
    assert isinstance(config, JarvisConfig)
    assert config.default_provider == "anthropic"
    assert config.providers["anthropic"].has_key()
    assert config.providers["anthropic"].api_key.get_secret_value() == FILE_KEY
    # blank placeholder key becomes "no key"
    assert not config.providers["openai"].has_key()


def test_env_override_beats_file(tmp_path: Path) -> None:
    _write_config(tmp_path)
    config = load_config(base_dir=tmp_path, env={"ANTHROPIC_API_KEY": ENV_KEY})
    assert config.providers["anthropic"].api_key.get_secret_value() == ENV_KEY


def test_env_fills_missing_file_key(tmp_path: Path) -> None:
    _write_config(tmp_path)
    config = load_config(base_dir=tmp_path, env={"OPENAI_API_KEY": ENV_KEY})
    assert config.providers["openai"].has_key()
    assert config.providers["openai"].api_key.get_secret_value() == ENV_KEY


# -- friendly errors --------------------------------------------------------


def test_missing_config_friendly_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigNotFoundError) as exc:
        load_config(base_dir=tmp_path, env={})
    msg = str(exc.value)
    assert msg == (
        "No provider config found.\n"
        "Run: jarvis init\n"
        "Then edit: .jarvis/config.toml"
    )


def test_missing_key_friendly_error(tmp_path: Path) -> None:
    _write_config(tmp_path)
    config = load_config(base_dir=tmp_path, env={})
    with pytest.raises(MissingAPIKeyError) as exc:
        require_provider(config, "openai")
    msg = str(exc.value)
    assert msg == (
        "No API key found for provider 'openai'.\n"
        "Add it to .jarvis/config.toml or set OPENAI_API_KEY."
    )


def test_require_provider_returns_default(tmp_path: Path) -> None:
    _write_config(tmp_path)
    config = load_config(base_dir=tmp_path, env={})
    name, provider = require_provider(config)
    assert name == "anthropic"
    assert provider.has_key()


def test_invalid_default_provider_rejected(tmp_path: Path) -> None:
    body = """
default_provider = "ghost"
[providers.anthropic]
api_key = ""
model = "m"
"""
    _write_config(tmp_path, body)
    problems = validate_config_file(base_dir=tmp_path, env={})
    assert problems
    assert any("ghost" in p for p in problems)


def test_validate_reports_missing_keys(tmp_path: Path) -> None:
    _write_config(tmp_path)
    problems = validate_config_file(base_dir=tmp_path, env={})
    assert any("openai" in p and "no api key" in p.lower() for p in problems)


def test_validate_clean_config_has_no_problems(tmp_path: Path) -> None:
    body = """
default_provider = "anthropic"
[providers.anthropic]
api_key = "%s"
model = "claude-opus-4-8"
""" % FILE_KEY
    _write_config(tmp_path, body)
    assert validate_config_file(base_dir=tmp_path, env={}) == []


# -- secret handling --------------------------------------------------------


def test_secretstr_not_in_repr(tmp_path: Path) -> None:
    _write_config(tmp_path)
    config = load_config(base_dir=tmp_path, env={})
    assert FILE_KEY not in repr(config)
    assert FILE_KEY not in str(config)
    assert FILE_KEY not in repr(config.providers["anthropic"])


def test_loading_registers_key_with_redactor(tmp_path: Path) -> None:
    _write_config(tmp_path)
    load_config(base_dir=tmp_path, env={"ANTHROPIC_API_KEY": ENV_KEY})
    # Both the file key and the env-override key must be scrubbed.
    assert jlog.redact(f"key is {FILE_KEY} ok") == "key is ***REDACTED*** ok"
    assert jlog.redact(f"key is {ENV_KEY} ok") == "key is ***REDACTED*** ok"


def test_redacting_filter_scrubs_log_output(tmp_path: Path) -> None:
    _write_config(tmp_path)
    load_config(base_dir=tmp_path, env={})

    buffer = io.StringIO()
    logger = logging.getLogger("jarvis_code.test.redaction")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = logging.StreamHandler(buffer)
    handler.addFilter(jlog.RedactingFilter())
    logger.addHandler(handler)

    # Even a careless caller logging the raw key cannot leak it.
    logger.warning("connecting with key=%s", FILE_KEY)
    output = buffer.getvalue()
    assert FILE_KEY not in output
    assert "***REDACTED***" in output


def test_no_key_in_simulated_transcript(tmp_path: Path) -> None:
    _write_config(tmp_path)
    load_config(base_dir=tmp_path, env={})
    # A transcript writer routes text through redact() before persisting.
    transcript_line = jlog.redact(
        f'{{"role":"system","text":"using key {FILE_KEY}"}}'
    )
    assert FILE_KEY not in transcript_line
