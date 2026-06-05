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
model = "your-deployment-name"                # Azure deployment name (NOT the model id)
endpoint = "https://your-resource.openai.azure.com"
api_version = "2024-06-01"
# endpoint_style: leave unset to auto-detect from the endpoint. Three shapes:
#   - classic Azure OpenAI: https://<res>.openai.azure.com   (deployment in URL)
#   - Foundry inference:     https://<res>.services.ai.azure.com  -> /models/chat/completions
#   - OpenAI-compatible v1:  https://<res>.services.ai.azure.com/openai/v1/  (model in body)
# Force with "azure_openai" | "foundry" | "openai_v1" if auto-detection is wrong.
# endpoint_style = "openai_v1"

[agent]
# How hard the agent works (override per run with --effort):
#   low    — quick & minimal, capped steps
#   medium — balanced (default)
#   high   — thorough; reasons about edge cases and verifies
#   madmax — maximum rigor; unlimited steps + sub-agents (slower, costs more)
effort = "medium"

[voice]
enabled = true                  # voice output on by default; --voice false to mute
engine = "system"               # piper | system | noop (system = macOS `say`)
default_voice = "jarvis-inspired"   # maps to a British male `say` voice (Daniel)
model_path = ""
config_path = ""
fallback_engine = "system"
speech_rate = 1.25              # spoken pace ×default; brisk by default, up to 3.0
volume = 0.9
# pitch/robotic shape the voice character (macOS `say`): pitch 1.0 = natural,
# lower = deeper; robotic 0 = natural intonation, up to 1 = flat/synthetic.
pitch = 1.0
robotic = 0.0
announce_subagents = true
announce_permissions = true
# speak_intent: when true, spoken status may include a short, model-written
# reason (e.g. "Running a command to install dependencies"), capped and
# secret-redacted. Default false speaks ONLY fixed curated phrases. Turning this
# on relaxes the "curated-only" privacy rule: a reason could echo a path or
# argument that isn't a registered secret, so it can't be guaranteed clean.
speak_intent = false
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
