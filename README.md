# jarvis-code

A local, sandboxed, multi-provider CLI coding agent with optional local/offline
voice I/O. Installs a command called **`jarvis`**.

> The agent operates **only** inside its sandbox root. The model *proposes*
> actions; the deterministic runtime *enforces* the rules. API keys never enter
> the model context, logs, transcripts, or speech. Voice is fully optional and
> local-by-default.

---

## Install

**One command** — clone, install everything, and put `jarvis` on your PATH:

```bash
git clone https://github.com/Hakeem-Hannoon/jarvis-code.git && cd jarvis-code && ./install.sh
```

That creates a virtualenv, installs jarvis **and all optional features** (neural
voice output + local speech-to-text), downloads the realistic British-male
"JARVIS" voice, and creates a per-user config — so `jarvis` runs from **any**
directory.

Then just run it:

```bash
jarvis
```

Add a model API key before your first message — edit `~/.config/jarvis/config.toml`
(or `export AZURE_API_KEY=...`). Run `jarvis doctor` anytime to check your setup.

<details><summary>Manual install (no install.sh)</summary>

```bash
python3 -m venv .venv && . .venv/bin/activate     # Python 3.11+
pip install -e ".[voice,stt,dev]"   # all features (use ".[dev]" alone for text-only)
jarvis init --global                # per-user config, used from any directory
```
Then put `jarvis` on your PATH, e.g. `ln -s "$PWD/.venv/bin/jarvis" ~/.local/bin/jarvis`.
</details>

---

## A quick tour

Run **`jarvis`** with no arguments for a **live chat** — a persistent
conversation where each line is a turn and context carries across turns.

- **Just type.** Ask for what you want and press Enter. The agent reads code,
  makes edits, and runs commands (asking first in `auto` mode), then reports back.
- **Full line editing.** ←/→ to move, **↑/↓ for history** (saved across
  sessions), and Emacs shortcuts: Ctrl-A/E (start/end), Ctrl-K/U (kill line),
  Ctrl-W (delete word).
- **It talks.** Voice output is **on by default** — short spoken status in a
  realistic neural voice (it narrates *what it's doing and why*). Mute a run with
  `--voice false`; force it on with `--voice true`.
- **Talk to it.** Type **`/talk`**, speak, press Enter — your speech is
  transcribed **locally** and run as the next turn. (`install.sh` already set up
  the speech engine.)
- **In-chat commands:** `/help` · `/talk` · `/mode plan|auto|dangerous` ·
  `/sandbox` · `/exit` (or Ctrl-D).

Prefer a one-shot run instead of a chat?

```bash
jarvis "add type hints to utils.py and run the tests"
jarvis --mode plan "explain how auth works in this project"
```

The agent runs with **no step limit** by default, so it works a task through to
completion — interrupt anytime with Ctrl-C.

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

## Voice (local & offline)

Everything voice-related runs **on your machine** — no audio or text is sent to a
cloud service unless you explicitly opt in (`allow_cloud_stt = true`).

### Output — jarvis speaks (on by default)

`install.sh` sets this up for you. It speaks **short status** as it works — and,
because the model narrates intent, *what it's trying to build* ("Refactoring the
parser to handle nested calls"), not just "writing a file".

```bash
jarvis voices test     # speak a test phrase; prints which voice is in use
jarvis voices list     # engine availability + what would be used
jarvis --voice false   # mute output for a run   (--voice true forces it on)
```

Engines (config `[voice] engine`): **piper** (local neural — the realistic
default, e.g. the British `alan` model `install.sh` downloads) · **system**
(macOS `say` / pyttsx3) · **noop** (silent fallback). Selection falls back
gracefully, so it never crashes if a voice is missing.

> **Want an even better voice?** On macOS you can download a Premium neural
> system voice (System Settings → Accessibility → Spoken Content → Manage
> Voices). To use a specific voice, set `[voice] default_voice` or point
> `model_path` at any Piper `.onnx`. See `docs/voice.md`.

**Privacy:** only short, curated/redacted status is spoken — never secrets, file
contents, or shell output. The model's one-line *reason* is spoken only when
`speak_intent = true` (the installed default); set it to `false` for fixed
phrases only. `"jarvis-inspired"` is a *style*, not a cloned actor voice.

### Input — talk to jarvis with `/talk`

In a chat, type **`/talk`** (or `/ptt`): speak, press **Enter** to stop, and your
words are transcribed **locally** (faster-whisper) and run as your next turn. The
first use downloads a small model once, then it's fully offline. If voice input
is off, Muted, or the engine is missing, `/talk` tells you exactly what to do.

Enable it in config (`install.sh`’s default does this):

```toml
[voice.input]
enabled = true
mode = "push_to_talk"     # open_mic | push_to_talk | muted
```

- **Push to Talk** — capture only between `/talk` and Enter.
- **Open Mic** — continuous capture (VAD-gated).
- **Muted** — the mic is fully off: **nothing is recorded, transcribed, or
  stored.**

Output and input are independent — enable either alone.

---

## Commands

```
jarvis                                start an interactive chat session (REPL)
jarvis "<task>"                       run the agent on a single task, then exit
jarvis --mode plan|auto|dangerous     select permission mode (default: auto)
jarvis --voice true|false             voice output on/off for this run (default: on)
jarvis --sandbox <path>               set the sandbox root
jarvis --provider <p> --model <m>     override provider/model
jarvis init [--global]                scaffold .jarvis/  (or a per-user global config)
jarvis doctor                         environment + config health checks
jarvis config path | validate         config helpers
jarvis voices list | test             voice engine info (test prints the active voice)
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
