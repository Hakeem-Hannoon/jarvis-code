# Architecture

jarvis-code is built as narrow, typed modules with clear seams. The guiding
principle: **the model proposes, the deterministic runtime enforces.**

## Module map

```
cli/         Typer app, command wiring, terminal UX (reporter, prompts)
agent/       runtime loop, context + compaction, project memory, system prompt
config/      pydantic schema, TOML loader, env overrides, secret handling
models/      ModelProvider protocol + per-provider HTTP clients
permissions/ mode policy matrix, permission manager, approval prompters
sandbox/     path canonicalization + enforcement, safe fs primitives
tools/       tool registry + implementations + the permission-gated executor
voice/       TTS engines (Piper/System/Noop), curated messages, speech queue
input/       STT engines, mic modes, the mic controller
events/      event bus, instruction queue + classifier, keyboard, queue prompter
subagents/   orchestrator + agent types
sessions/    append-only JSONL transcript writer
utils/       secret-redacting logging, diffs
```

## Request lifecycle (one run)

1. **CLI** resolves the sandbox root, loads config (friendly errors if missing),
   and assembles a runtime.
2. **assemble_runtime** builds the provider (from config/flags), the tool
   registry, the permission manager (with a prompter), the session writer,
   loads project memory, builds the system prompt, and constructs the
   sub-agent orchestrator. Keys are read here and handed only to the provider.
3. **AgentRuntime.run** loops:
   - drain the live instruction queue at safe checkpoints (steering, questions,
     interrupts);
   - compact context if over budget (preserving goal/files/commands/errors/…);
   - call the provider (off the event loop via `to_thread`);
   - parse text + tool calls; for each tool call, route through the permission
     manager; execute allowed tools through the sandbox; append results;
   - fold any "Do Something Else" feedback back in as steering;
   - stop on completion, a hard interrupt (ABORTED), or the step limit.
4. Everything is recorded to an append-only JSONL transcript (redacted).

## Trust boundary

- Tools never decide permissions; the `ToolExecutor` asks the
  `PermissionManager` first and only runs allowed tools.
- Tools never touch the filesystem directly; all access goes through the single
  `Sandbox` chokepoint, which canonicalizes and re-validates every path.
- Providers hold the API key privately and place it only in HTTP headers.

## Concurrency

The runtime is async so live steering and non-blocking voice work cleanly.
Provider calls run via `asyncio.to_thread`. Sub-agents run their own loop on a
worker thread so they can be launched synchronously from inside the parent's
running loop while staying isolated.
