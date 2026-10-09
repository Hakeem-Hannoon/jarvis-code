# Security model

## Sandbox (the filesystem chokepoint)

All filesystem access goes through one `Sandbox` object. For every path:

1. expand `~` and `$VAR`;
2. make it absolute against the sandbox root;
3. canonicalize with **symlinks resolved** and `..` normalized;
4. re-validate that the canonical result is inside the root.

Rejected (raise `SandboxViolation`, logged): `..` traversal, absolute
out-of-root paths, and symlinks whose targets resolve outside the root —
including an in-root directory that *contains* a symlink pointing out.

`delete`/`move` act on a symlink **as a link** (they never dereference it out of
the sandbox), so an in-root symlink can be cleaned up without touching its
target. Reads/writes always follow symlinks and therefore cannot escape.

Subprocess working directories are validated the same way (`validate_cwd`).
**Even `dangerous` mode cannot escape the sandbox** — the mode only governs
whether you're asked before an action, not where it can reach.

## Shell isolation (a documented deferral)

`run_shell` enforces a sandbox-confined working directory (the real guarantee)
and screens a small, high-confidence set of catastrophic patterns in every mode
(`rm -rf /`, fork bombs, `mkfs`, device writes, power control). Screening looks
at the *program being run*, so `grep -r shutdown .` is fine while `rm -Rf /`,
`rm -r -f /` and `sh -c 'rm -rf ~'` are all caught. Per the invariants we do
**not** rely on string matching for containment.

Commands run with stdin attached to `/dev/null` (they cannot read your
keystrokes) and in their own process group, so a timeout or Ctrl-C kills
everything the command started.

Preventing a shell from *reading* outside the root via absolute paths (e.g.
`cat /etc/passwd`) requires OS-level sandboxing (containers / seccomp /
`sandbox-exec`). That is deferred. Until it lands, containment rests on: cwd
confinement + `plan` forbidding shell + `auto` asking before every shell command
(showing command, cwd, why, expected effect, risk) + full logging. The
deterministic *fs tools* always enforce canonical in-root paths.

## Approvals

- **An approval is always given to a request you can see.** Text you typed (or
  spoke) earlier may *decline or redirect* the next request — "no", or "use a
  temp dir instead" — but it can never approve one. A stray `ok` typed while the
  agent was working is discarded rather than pre-authorizing the next shell
  command; the menu is shown and you answer it.
- Everything in the approval block (command, cwd, reason, diff) is
  model-controlled, so control characters are rendered visibly (`\x1b[2K`)
  instead of being sent to the terminal, where they could hide part of a command.
- With no interactive input available (stdin closed / piped), `auto` mode fails
  closed: the request is denied.
- `move_file` never overwrites an existing destination, and `edit_file` rejects
  an empty `old_string` — you approve the change you were shown, nothing wider.

## Interrupts

Ctrl-C (or typing `stop`) ends the **current turn**, including a model call in
flight and a running shell command (its whole process tree is killed). The chat
session and its context survive; Ctrl-C at the idle prompt leaves the chat.

## Secrets

- Keys are typed as `SecretStr` (never shown in `repr`/`str`/model dumps).
- They are read once at config load, registered with a process-wide redactor,
  and handed to the provider for use **only** in HTTP request headers — never in
  the URL, request body, model context, logs, transcripts, or speech.
- A `RedactingFilter` scrubs any registered key from every log record; the
  session transcript writer routes every field through the same redactor.
- The config holding keys lives under `.jarvis/`, which `jarvis init` makes
  git-ignored by default (`.jarvis/.gitignore` = `*`) and owner-only (`0600`).
- The agent's file tools cannot read, search, write, edit, move or delete
  jarvis's own config (`.jarvis/config.toml`, `.jarvis.toml`) in any mode —
  redaction only covers the keys that were *loaded*, and a config the agent can
  write is one a prompt injection could repoint at another endpoint.
- Subprocesses run with credential-shaped environment variables removed
  (`*_API_KEY`, `*_TOKEN`, `*_SECRET`, `*PASSWORD`, … and bare `API_KEY`/`TOKEN`).

> **Know the limit:** a project-local config is trusted. Running `jarvis` in a
> directory you did not create means its `.jarvis.toml`, if any, chooses the
> endpoint your key is sent to. Prefer the global config (`jarvis init --global`)
> when working in checkouts you don't fully trust.

## Voice privacy

- Local/offline by default. No cloud TTS/STT unless explicitly opted in
  (`allow_cloud_stt = true`).
- Only short, curated status strings are spoken — never raw model output, file
  contents, shell output, or secrets. Permission requests voice the program
  name only (never command arguments).
- `Muted` mic mode records, transcribes, and stores nothing. Audio is a
  transient local; only transcribed text is ever surfaced or logged.
