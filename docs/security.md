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
(`rm -rf /`, fork bombs, `mkfs`, device writes, power control). Per the
invariants we do **not** rely on string matching for containment.

Preventing a shell from *reading* outside the root via absolute paths (e.g.
`cat /etc/passwd`) requires OS-level sandboxing (containers / seccomp /
`sandbox-exec`). That is deferred. Until it lands, containment rests on: cwd
confinement + `plan` forbidding shell + `auto` asking before every shell command
(showing command, cwd, why, expected effect, risk) + full logging. The
deterministic *fs tools* always enforce canonical in-root paths.

## Secrets

- Keys are typed as `SecretStr` (never shown in `repr`/`str`/model dumps).
- They are read once at config load, registered with a process-wide redactor,
  and handed to the provider for use **only** in HTTP request headers — never in
  the URL, request body, model context, logs, transcripts, or speech.
- A `RedactingFilter` scrubs any registered key from every log record; the
  session transcript writer routes every field through the same redactor.
- The config holding keys lives under `.jarvis/`, which `jarvis init` makes
  git-ignored by default (`.jarvis/.gitignore` = `*`).

## Voice privacy

- Local/offline by default. No cloud TTS/STT unless explicitly opted in
  (`allow_cloud_stt = true`).
- Only short, curated status strings are spoken — never raw model output, file
  contents, shell output, or secrets. Permission requests voice the program
  name only (never command arguments).
- `Muted` mic mode records, transcribes, and stores nothing. Audio is a
  transient local; only transcribed text is ever surfaced or logged.
