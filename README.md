# jarvis-code

A local, sandboxed, multi-provider CLI coding agent with optional local/offline
voice I/O. Installs a command called **`jarvis`**.

> The agent operates **only** inside its sandbox root. The model *proposes*
> actions; the deterministic runtime *enforces* the rules. Secrets never enter
> the model context, logs, transcripts, or speech. Voice is fully optional and
> local-by-default.

## Quick start

```bash
git clone <repo-url> jarvis-code
cd jarvis-code

python3 -m venv .venv && . .venv/bin/activate   # Python 3.11+
pip install -e ".[dev]"

jarvis --help
jarvis doctor            # environment & config health checks (coming in Phase 6)
jarvis init              # scaffold a git-ignored .jarvis/ with config (Phase 6)
jarvis --mode plan "explain this project"
```

## Status

Early development. Built in phases (see `instructions.txt`):

- [x] **Phase 0** — Scaffold & toolchain
- [ ] Phase 1 — Sandbox filesystem layer
- [ ] Phase 2 — Config & secrets loader
- [ ] Phase 3 — Tool registry + permission manager
- [ ] Phase 4 — Model provider abstraction
- [ ] Phase 5 — Agent runtime loop, sessions, memory
- [ ] Phase 6 — CLI surface & terminal UX
- [ ] Phase 7 — Voice output (local/offline)
- [ ] Phase 8 — Voice input + STT + mic modes
- [ ] Phase 9 — Event system, live steering & interrupts
- [ ] Phase 10 — Sub-agent orchestration
- [ ] Phase 11 — Docs, final doctor, polish

## Security model (summary)

- **Sandbox:** every filesystem path is canonicalized and re-validated to be
  inside the sandbox root before use. Symlink escapes are rejected. Subprocesses
  run with a cwd confined to the root. Even `dangerous` mode cannot escape.
- **Permission modes:** `plan` (read-only), `auto` (asks before shell / shows
  diffs), `dangerous` (no per-action prompts, still sandboxed).
- **Secrets:** API keys live in a git-ignored local config or env vars, and are
  never placed in model context, logs, transcripts, or speech.
- **Voice privacy:** local/offline by default; no cloud TTS/STT without explicit
  opt-in; `Muted` mode records nothing.

Full provider setup, voice setup, and keyboard controls are documented in
Phase 11. See `CLAUDE.md` for engineering invariants.

## Development

```bash
pytest -q          # the full test suite, one command, must stay green
```

## License

MIT
