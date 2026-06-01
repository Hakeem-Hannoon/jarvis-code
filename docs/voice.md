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
speak_intent = false          # opt-in: speak a short model-written reason too
swarm_phrase = "Launching the swarm."

[voice.output]
max_chars = 160               # never speak strings longer than this
```

Engine selection order: configured → fallback → Noop (first available wins).
Use `jarvis voices list` to see availability and what would be used, and
`jarvis voices test` to hear a short phrase.

On **macOS the `system` engine uses the built-in `say` command** — no install
needed, and it plays reliably (jarvis speaks from a worker thread, where the
pyttsx3 driver is silent on macOS). `pyttsx3` is used as the `system` backend on
other platforms.

**Voice realism (macOS).** The `default_voice = "jarvis-inspired"` preset maps to
a British male `say` voice and auto-prefers a natural **Premium/Enhanced** neural
variant if installed (e.g. `Oliver (Premium)`, `Daniel (Premium)`), falling back
to the robotic compact `Daniel` otherwise. Download a premium voice once via
**System Settings → Accessibility → Spoken Content → System Voice → Manage
Voices → English (UK)** and jarvis uses it automatically. `jarvis voices test`
prints the voice it will use. You can also set `default_voice` to any exact `say`
voice name. (Voice output is on by default; mute a run with `--voice false`.)

### What is spoken

Only short, curated status strings from a fixed catalog (e.g. "Scanning the
project.", "Running the test suite.", "Requesting permission to run a
command."). The voice subsystem has no path that speaks raw model output, file
contents, shell output, or secrets. Pending speech is cancellable the moment you
approve/deny/interrupt.

`"jarvis-inspired"` is a style preset (calm, concise, technical, cinematic) — it
does not clone any real actor or character voice.

### Descriptive narration (`speak_intent`, opt-in)

By default only fixed catalog phrases are spoken. Set `speak_intent = true` to
also voice a **short, model-written reason** for what it's doing (e.g. "Running
a command to install dependencies" instead of just "Running a command"). The
reason is reduced to one line, length-capped, and run through the secret
redactor, and is spoken at most once per model turn.

This relaxes the strict "curated-only" rule: a reason is model-authored text, so
it could echo a path or argument that isn't a registered secret. Leave it off
(the default) if you need the hard guarantee that only fixed phrases are spoken.

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

### Speaking to jarvis in chat: `/talk`

In an interactive `jarvis` session, type **`/talk`** (or `/ptt`) to push-to-talk:
it records from the mic, you press **Enter** to stop, it transcribes locally, and
the transcript becomes your next message. Requirements:

```bash
pip install -e ".[stt]"        # faster-whisper / vosk + sounddevice
```

and in config: `[voice.input] enabled = true` with `mode = "push_to_talk"` (or
`open_mic`). If voice input is off, Muted, or the mic/STT backend isn't
installed, `/talk` prints a short, actionable reason and nothing is recorded.

## Keyboard controls (interactive voice mode)

```
M       cycle mic mode (Open Mic / Push to Talk / Muted)
V       toggle push-to-talk recording
Space   hold-to-talk in Push to Talk mode
Esc     soft interrupt (finish the current safe step, then adjust)
Ctrl+C  hard stop / exit
?       show controls
```
