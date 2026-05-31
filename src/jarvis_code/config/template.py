"""The config template written by ``jarvis init`` (placeholders only)."""

from __future__ import annotations

# Kept in sync with .jarvis.example.toml at the repo root.
DEFAULT_CONFIG_TEMPLATE = """\
# jarvis-code configuration. Fill in a real key for one provider, OR leave
# api_key blank and export the matching env var (e.g. ANTHROPIC_API_KEY).
# Env vars always override file values. This file lives under .jarvis/ which is
# git-ignored by default, so it is safe to put keys here.

default_provider = "anthropic"

[providers.anthropic]
api_key = ""                                  # or set ANTHROPIC_API_KEY
model = "claude-opus-4-8"

[providers.openai]
api_key = ""                                  # or set OPENAI_API_KEY
model = "gpt-4o"

[providers.deepseek]
api_key = ""                                  # or set DEEPSEEK_API_KEY
model = "deepseek-chat"

[providers.gemini]
api_key = ""                                  # or set GEMINI_API_KEY
model = "gemini-1.5-pro"

[providers.vertex]
api_key = ""                                  # or set VERTEX_API_KEY
model = "gemini-1.5-pro"
endpoint = "https://us-central1-aiplatform.googleapis.com"
project = "your-gcp-project-id"
location = "us-central1"

[providers.azure]
api_key = ""                                  # or set AZURE_API_KEY
model = "your-deployment-name"
endpoint = "https://your-resource.openai.azure.com"
api_version = "2024-06-01"

[voice]
enabled = false
engine = "piper"                # piper | system | noop
default_voice = "jarvis-inspired"
model_path = ""
config_path = ""
fallback_engine = "system"
speech_rate = 1.0
volume = 0.9
announce_subagents = true
announce_permissions = true
swarm_phrase = "Launching the swarm."

[voice.output]
max_chars = 160

[voice.input]
enabled = false
mode = "muted"                  # open_mic | push_to_talk | muted
stt_engine = "faster-whisper"   # faster-whisper | vosk
push_to_talk_key = "space"
wake_word_enabled = false
allow_cloud_stt = false         # MUST be explicitly true to ever use cloud STT
"""
