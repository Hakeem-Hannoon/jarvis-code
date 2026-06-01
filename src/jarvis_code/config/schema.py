"""Pydantic v2 schema mirroring ``.jarvis.example.toml``.

API keys are typed as ``SecretStr`` so they never appear in ``repr``/``str``,
model dumps, or log records. The loader (``config.loader``) additionally
registers each resolved key with the global redactor.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class MicMode(str, Enum):
    OPEN_MIC = "open_mic"
    PUSH_TO_TALK = "push_to_talk"
    MUTED = "muted"


class VoiceEngineName(str, Enum):
    PIPER = "piper"
    SYSTEM = "system"
    NOOP = "noop"


class SttEngineName(str, Enum):
    FASTER_WHISPER = "faster-whisper"
    VOSK = "vosk"


_STRICT = ConfigDict(extra="forbid")


class ProviderConfig(BaseModel):
    """One model provider's settings. ``api_key`` may come from env instead."""

    model_config = _STRICT

    api_key: SecretStr | None = None
    model: str
    endpoint: str | None = None
    api_version: str | None = None
    project: str | None = None
    location: str | None = None
    # Azure only: "azure_openai" | "foundry". Default None auto-detects by host
    # (an *.services.ai.azure.com endpoint is treated as Azure AI Foundry).
    endpoint_style: str | None = None

    @field_validator("api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, v: object) -> object:
        # Treat an empty placeholder ("") in the example/template as "unset"
        # so env vars cleanly take over and missing-key detection works.
        if v is None:
            return None
        if isinstance(v, SecretStr):
            return None if v.get_secret_value() == "" else v
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    def has_key(self) -> bool:
        return self.api_key is not None and self.api_key.get_secret_value() != ""


class VoiceOutputConfig(BaseModel):
    model_config = _STRICT

    max_chars: int = Field(default=160, ge=1)


class VoiceInputConfig(BaseModel):
    model_config = _STRICT

    enabled: bool = False
    mode: MicMode = MicMode.MUTED
    stt_engine: SttEngineName = SttEngineName.FASTER_WHISPER
    push_to_talk_key: str = "space"
    wake_word_enabled: bool = False
    # Cloud STT must be explicitly opted into; default off enforces local-only.
    allow_cloud_stt: bool = False


class VoiceConfig(BaseModel):
    model_config = _STRICT

    enabled: bool = False
    engine: VoiceEngineName = VoiceEngineName.PIPER
    default_voice: str = "jarvis-inspired"
    model_path: str = ""
    config_path: str = ""
    fallback_engine: VoiceEngineName = VoiceEngineName.SYSTEM
    speech_rate: float = Field(default=1.0, gt=0)
    volume: float = Field(default=0.9, ge=0, le=1)
    announce_subagents: bool = True
    announce_permissions: bool = True
    # Opt-in: when true, spoken status may include a one-line, model-authored
    # reason (capped + secret-redacted). Default false keeps the strict
    # "only curated strings are spoken" privacy invariant in force.
    speak_intent: bool = False
    swarm_phrase: str = "Launching the swarm."
    output: VoiceOutputConfig = Field(default_factory=VoiceOutputConfig)
    input: VoiceInputConfig = Field(default_factory=VoiceInputConfig)


class JarvisConfig(BaseModel):
    model_config = _STRICT

    default_provider: str = "anthropic"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)

    @model_validator(mode="after")
    def _default_provider_exists(self) -> "JarvisConfig":
        if self.providers and self.default_provider not in self.providers:
            raise ValueError(
                f"default_provider '{self.default_provider}' is not configured "
                f"under [providers]. Known: {sorted(self.providers)}"
            )
        return self
