# Voice (local-by-default)

Voice is entirely optional and off until enabled in config. Install the extras
only if you want it:

```bash
pip install -e ".[voice]"   # TTS: piper-tts, pyttsx3, sounddevice
pip install -e ".[stt]"     # STT: faster-whisper, vosk, sounddevice, webrtcvad
```

Everything degrades cleanly: with nothing installed, output uses the silent
Noop engine and input is disabled — no crashes.

## Output (TTS)

Configured under `[voice]`:

```toml
[voice]
enabled = true
engine = "piper"              # piper | system | noop
fallback_engine = "system"    # used if the primary is unavailable
default_voice = "jarvis-inspired"
model_path = "/path/to/voice.onnx"     # required for Piper
config_path = "/path/to/voice.onnx.json"
speech_rate = 1.0
volume = 0.9
announce_subagents = true
announce_permissions = true
swarm_phrase = "Launching the swarm."

[voice.output]
max_chars = 160               # never speak strings longer than this
```

Engine selection order: configured → fallback → Noop (first available wins).
Use `jarvis voices list` to see availability and what would be used, and
`jarvis voices test` to hear a short phrase.

### What is spoken

Only short, curated status strings from a fixed catalog (e.g. "Scanning the
project.", "Running the test suite.", "Requesting permission to run a
command."). The voice subsystem has no path that speaks raw model output, file
contents, shell output, or secrets. Pending speech is cancellable the moment you
approve/deny/interrupt.

`"jarvis-inspired"` is a style preset (calm, concise, technical, cinematic) — it
does not clone any real actor or character voice.

## Input (STT) + mic modes

Configured under `[voice.input]`:

```toml
[voice.input]
enabled = true
mode = "push_to_talk"         # open_mic | push_to_talk | muted
stt_engine = "faster-whisper" # faster-whisper | vosk  (both local)
push_to_talk_key = "space"    # Space holds to talk; V toggles recording
wake_word_enabled = false
allow_cloud_stt = false       # MUST be explicitly true to use any cloud STT
```

- **Open Mic**: continuous capture, VAD/wake-word gated to avoid noise.
- **Push to Talk**: records only between press and release.
- **Muted**: the mic is fully off — the recorder is never even invoked.

Cloud STT is refused unless `allow_cloud_stt = true`; the default path is fully
local. Audio is never persisted — only the transcribed text instruction may be
kept for continuity.

## Keyboard controls (interactive voice mode)

```
M       cycle mic mode (Open Mic / Push to Talk / Muted)
V       toggle push-to-talk recording
Space   hold-to-talk in Push to Talk mode
Esc     soft interrupt (finish the current safe step, then adjust)
Ctrl+C  hard stop / exit
?       show controls
```
