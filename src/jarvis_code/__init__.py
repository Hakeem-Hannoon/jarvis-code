"""jarvis-code — a local, sandboxed, multi-provider CLI coding agent.

The package is organized into narrow modules with clear seams:

    cli/         Typer app, command wiring, terminal UI
    agent/       runtime loop, context management, compaction
    config/      loader, pydantic schema, secrets resolution
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

Security invariants live in CLAUDE.md and are enforced by the deterministic
runtime (sandbox + permissions), never by the model.
"""

__version__ = "0.1.0"
