# CLAUDE.md — jarvis-code

Guidance for any future session working in this repo. Read this first.

## What this is

`jarvis-code` is an open-source CLI coding agent that installs a command named
`jarvis`. Think "Claude Code, but local-first": strict sandboxing, explicit
permission modes, multi-provider model support, local/offline voice I/O, live
voice steering, and sub-agent orchestration. It is built to be production-quality
foundation code, not a demo.

## Hard invariants (NEVER violate — stop and ask if a change would)

**Sandbox**
- The agent operates only inside the resolved sandbox root (launch dir, or
  `--sandbox <path>`). Never read/write/delete/move/execute outside it.
- All paths are canonicalized (resolve symlinks, `..`, env/shell expansion)
  **before** use and re-checked to be inside the root. Symlink targets resolving
  outside the root are rejected.
- Subprocess cwd must be the sandbox root or a validated subdir.
- Don't rely on string matching for escapes — enforce on canonical resolved
  paths in the deterministic tool layer. Every escape attempt is blocked **and**
  logged. Even `dangerous` mode cannot escape the sandbox.

**Secrets**
- API keys never enter model context, are never printed, never written to logs
  or transcripts, never spoken. The local config holding keys is git-ignored.

**Trust boundary**
- The model *proposes*; the deterministic runtime *enforces*. Treat every
  model-produced path/command/argument as untrusted input and validate it.

**Voice privacy**
- Voice in/out is local/offline by default. No cloud TTS/STT unless explicitly
  opted in via config. Never send source/file contents/shell output/secrets/raw
  audio to any voice/cloud service by default. Never store raw mic audio unless
  opted in. `Muted` records/transcribes/stores nothing. UI must show mic state.
  Only short curated status strings are spoken — never raw model output.

## Layout

```
src/jarvis_code/
  cli/         Typer app, command wiring, terminal UI
  agent/       runtime loop, context mgmt, compaction
  config/      loader, schema (pydantic), secrets resolution
  models/      provider abstraction + per-provider clients
  permissions/ permission manager, mode policies, prompt menu
  sandbox/     path canonicalization + enforcement, safe fs ops
  tools/       tool registry + tool implementations
  voice/       TTS engines (Piper/System/Noop) + queue
  input/       STT engines + mic modes + VAD
  events/      event types + bus + instruction queue
  subagents/   orchestrator + agent types
  sessions/    JSONL transcript persistence
  utils/       logging (secret-redacting), diffs, helpers
tests/  docs/
```

## Tech stack (decided)

- Python 3.11+. CLI: **Typer**. Config types: **pydantic v2**. Config format:
  **TOML** (`tomllib` read + `tomli-w` write). HTTP: **httpx**. Tests: **pytest**
  (+ `pytest-asyncio`, async mode). Async runtime for live steering / non-blocking
  voice.
- Optional, lazy-imported, never hard requirements: `piper-tts`, `pyttsx3`,
  `faster-whisper`/`vosk`, `sounddevice`, `webrtcvad`. Gate every import; degrade
  to a Noop implementation when absent — clear actionable message, never a stack
  trace, never a hard failure of unrelated features.
- Packaging: console-script `jarvis = jarvis_code.cli.main:app`.

## Permission modes (exactly three)

- `plan`: read/inspect/search/plan only. No writes/edits/deletes/shell/installs/
  git mutations.
- `auto`: free reads; may write/edit after showing a concise diff/summary and
  getting approval where appropriate; **must ask before any shell command**,
  showing command, cwd, why, expected effect, risk. Approval menu is exactly
  **1) Yes  2) No  3) Do Something Else** (3 collects free-text steering).
- `dangerous`: read/write/edit/delete/move/shell without per-action asking,
  **still confined to the sandbox**. Prints a startup warning.

## How to work here

1. Build in phase order (see `instructions.txt`). Don't start a phase until the
   prior phase's tests pass.
2. Security first: sandbox (Phase 1) and config/secrets (Phase 2) before fun.
   Voice is last.
3. Test as you go — every phase shipping logic ships unit tests. Run the test
   command before declaring a phase done. Never leave the tree red.
4. Commit per phase with a scoped message.
5. Keep it small: small typed functions, narrow modules, clear seams. Boring,
   readable code over cleverness.
6. Ask before diverging on anything that breaks an invariant, changes the public
   CLI surface, or adds a required (non-optional) third-party dependency.

## Commands

```bash
# install (editable) into a venv
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# run the CLI
jarvis --help

# run tests (single command, must stay green)
pytest -q
```

## Known engineering decisions / deferrals

- **Shell process isolation:** `run_shell` *enforces* a sandbox-confined working
  directory (the real guarantee) and *screens* a small set of catastrophic
  patterns in all modes (`rm -rf /`, fork bombs, `mkfs`, device writes, power
  control) as defense in depth. Preventing a shell from reading outside the root
  via absolute paths (e.g. `cat /etc/passwd`) requires OS-level sandboxing
  (containers/seccomp/sandbox-exec), which is deferred. Until then containment
  rests on: cwd confinement + `plan` forbidding shell + `auto` asking before
  every shell command (showing command/cwd/why/effect/risk) + logging. Our
  deterministic *fs tools* always enforce canonical in-root paths.

## Status

All phases (0–11) complete:
- 0 scaffold/packaging/CLI; 1 sandbox (single fs chokepoint); 2 config +
  secret-redacting logger; 3 full tool set + 3-mode permission manager + 1/2/3
  menu; 4 six model providers (keys header-only); 5 async runtime loop +
  context compaction + JSONL sessions + project memory; 6 CLI surface
  (init/doctor/config/voices) + UX; 7 local voice output (Piper/System/Noop) +
  curated cancellable queue; 8 local STT + Open Mic/PTT/Muted; 9 event bus +
  instruction queue + steering/interrupts + voice-as-permission; 10 sub-agent
  orchestration (clamped perms, summary-only); 11 docs + final doctor.

Test command: `pytest -q` (must stay green). See `docs/` for deeper notes and
`instructions.txt` for the original phase plan.
