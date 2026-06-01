# jarvis-code

A local, sandboxed, multi-provider CLI coding agent with optional local/offline
voice I/O. Installs a command called **`jarvis`**.

> The agent operates **only** inside its sandbox root. The model *proposes*
> actions; the deterministic runtime *enforces* the rules. API keys never enter
> the model context, logs, transcripts, or speech. Voice is fully optional and
> local-by-default.

---

## Quick start

Clone and run once — `jarvis` then works from **any** directory:

```bash
git clone <repo-url> jarvis-code
cd jarvis-code
./install.sh                  # installs jarvis on PATH (pipx, or venv + ~/.local/bin)
                              # and creates a per-user global config

jarvis doctor                 # health checks (works before adding a key)
# add a key to ~/.config/jarvis/config.toml, OR export e.g. AZURE_API_KEY=...

cd ~/any/project
jarvis                        # interactive chat — picks up the global config anywhere
jarvis --mode plan "explain this project"   # or run a single task and exit
```

<details><summary>Manual install (no install.sh)</summary>

```bash
python3 -m venv .venv && . .venv/bin/activate     # Python 3.11+
pip install -e ".[dev]"
jarvis init --global          # per-user config, used from any directory
```
</details>

Running `jarvis` with no task drops you into a **live chat**: a persistent
conversation where each line is a turn and context carries across turns. In-chat
commands: `/help`, `/talk` (push-to-talk), `/mode plan|auto|dangerous`,
`/sandbox`, `/exit` (or Ctrl-D). The prompt has full line editing — arrow keys,
persistent ↑/↓ history, and Emacs shortcuts (Ctrl-A/E/K/U/W).

**Voice output is on by default** (local macOS `say`, British "Daniel" voice for
the JARVIS-style preset). Mute a run with `--voice false`, or force it on with
`--voice true`. The agent runs with **no step limit** by default.

`jarvis doctor` works before `jarvis init` and tells you exactly what's missing.
Optional voice extras: `pip install -e ".[voice]"` (TTS) and `".[stt]"` (speech
input) — everything degrades cleanly to silent/no-op when they're absent.

---

## Permission modes

Choose with `--mode` (default: `auto`).

| Mode        | Reads | Writes / edits / deletes / shell | Notes |
|-------------|-------|----------------------------------|-------|
| `plan`      | ✓     | ✗ (denied)                       | Read / inspect / plan only. |
| `auto`      | ✓     | asks first (shows a diff/summary; for shell: command, cwd, why, effect, risk) | The default. |
| `dangerous` | ✓     | no per-action prompt             | Prints a startup warning. **Still confined to the sandbox.** |

The approval prompt is always exactly:

```
  1) Yes   2) No   3) Do Something Else
```

**Do Something Else** lets you type (or speak) an instruction back to the model,
which it uses to revise its approach.

---

## Security model

- **Sandbox.** Every filesystem path is expanded (`~`, `$VAR`), made absolute,
  canonicalized with symlinks resolved, and re-validated to be inside the
  sandbox root *before* any use. Traversal (`..`), absolute out-of-root paths,
  and symlinks whose targets escape are all rejected and logged. Subprocesses
  run with a working directory confined to the root. **Even `dangerous` mode
  cannot escape the sandbox.**
- **Shell.** `run_shell` enforces a confined working directory and screens a
  small set of catastrophic patterns (e.g. `rm -rf /`, fork bombs). Full process
  isolation (preventing a shell reading outside via absolute paths) needs
  OS-level sandboxing and is a documented deferral; until then `plan` forbids
  shell and `auto` asks before every command. See `docs/security.md`.
- **Secrets.** API keys live in a git-ignored local config or env vars. They are
  used only as HTTP request headers and are **never** placed in the model
  context, URLs, logs, transcripts, or speech. A process-wide redactor scrubs
  any registered key from logs and transcripts.
- **Trust boundary.** The model proposes; the deterministic runtime enforces.
  Every model-produced path, command, and argument is treated as untrusted.

---

## Providers

Configure in `.jarvis/config.toml` (created by `jarvis init`) or via env vars.
Select at runtime with `--provider <name>` and `--model <name>`.

| Provider     | Config key            | Env var             | Notes |
|--------------|-----------------------|---------------------|-------|
| Anthropic    | `[providers.anthropic]` | `ANTHROPIC_API_KEY` | Messages API. |
| OpenAI       | `[providers.openai]`    | `OPENAI_API_KEY`    | Chat Completions. |
| DeepSeek     | `[providers.deepseek]`  | `DEEPSEEK_API_KEY`  | OpenAI-compatible. |
| Gemini       | `[providers.gemini]`    | `GEMINI_API_KEY`    | `generateContent`. |
| Vertex AI    | `[providers.vertex]`    | `VERTEX_API_KEY`    | Set `endpoint`, `project`, `location`. |
| Azure Foundry| `[providers.azure]`     | `AZURE_API_KEY`     | Set `endpoint`, `api_version`; `model` = deployment name. |

Env vars always override file values. Example:

```toml
default_provider = "anthropic"

[providers.anthropic]
api_key = ""                 # or export ANTHROPIC_API_KEY
model = "claude-opus-4-8"

[providers.azure]
api_key = ""                 # or export AZURE_API_KEY
model = "my-deployment"
endpoint = "https://my-resource.openai.azure.com"
api_version = "2024-06-01"
```

`jarvis config validate` reports problems (never printing a key); `jarvis config
path` prints the resolved config location.

---

## Voice (optional, local-by-default)

Voice is off by default. Enable it under `[voice]` / `[voice.input]` in config.
Nothing is sent to any cloud TTS/STT unless you explicitly opt in.

### Output (TTS)

```bash
pip install -e ".[voice]"
jarvis voices list      # shows engine availability + which would be used
jarvis voices test      # speaks a short test phrase (silent Noop if unavailable)
```

- **Piper** (default): local neural TTS. Download a `.onnx` voice model and set
  `model_path` (and `config_path`) under `[voice]`.
- **System**: OS TTS via `pyttsx3`.
- **Noop**: silent fallback — always available, never crashes.

Only short, curated status strings are ever spoken (e.g. "Running the test
suite."). Raw model output, file contents, shell output, and secrets are never
voiced. The default preset `"jarvis-inspired"` is a calm/concise/technical
*style* — not a cloned voice.

### Input (STT) + mic modes

```bash
pip install -e ".[stt]"
```

Set `[voice.input] enabled = true` and choose a `mode`:

- **Open Mic** — continuous listening (VAD/wake-word gated to avoid noise).
- **Push to Talk** — records only while held (`Space`); `V` toggles recording.
- **Muted** — the mic is fully off: **nothing is recorded, transcribed, or
  stored.**

Local STT (`faster-whisper` or `vosk`) is the default. Cloud STT is refused
unless you set `allow_cloud_stt = true`. The terminal shows the active mode:

```
Mic: [Open Mic] [>Push to Talk<] [Muted]
```

In an interactive session, type **`/talk`** to push-to-talk: speak your message,
press **Enter** to stop, and the local transcript becomes your next turn. If
voice input is off, Muted, or STT isn't installed, `/talk` says why and records
nothing.

Voice input and output are independent — you can enable one without the other.

---

## Keyboard controls (interactive voice mode)

```
M       cycle mic mode (Open Mic / Push to Talk / Muted)
V       toggle push-to-talk recording
Space   hold-to-talk in Push to Talk mode
Esc     soft interrupt (finish the current safe step, then adjust)
Ctrl+C  hard stop / exit
?       show controls
```

Live speech/text is classified into **steering** (folded into the plan),
**questions** (answered briefly while work continues), **soft/hard interrupts**,
and **permission responses** (routed to the active 1/2/3 prompt — one path, no
parallel permission system).

---

## Commands

```
jarvis                                start an interactive chat session (REPL)
jarvis "<task>"                       run the agent on a single task, then exit
jarvis --mode plan|auto|dangerous     select permission mode
jarvis --sandbox <path>               set the sandbox root
jarvis --provider <p> --model <m>     override provider/model
jarvis init                           scaffold .jarvis/
jarvis doctor                         environment + config health checks
jarvis config path | validate         config helpers
jarvis voices list | test             voice engine info
jarvis --help | --version
```

---

## Sub-agents

The agent can spawn focused sub-agents — **explorer, planner, debugger,
reviewer, implementer, verifier** — each in its own context window. A sub-agent
inherits the same sandbox and a permission mode clamped to never exceed the
parent's, and returns a concise summary (not its transcript).

---

## Development

```bash
pytest -q          # the full test suite, one command, must stay green
```

See `CLAUDE.md` for engineering invariants and `docs/` for deeper notes
(`architecture.md`, `security.md`, `voice.md`).

## License

MIT
