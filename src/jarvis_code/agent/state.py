"""RunState: the durable facts a run accumulates, preserved across compaction."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunState:
    """Key facts to preserve when older context is summarized away."""

    goal: str = ""
    plan: str = ""
    files_changed: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    subagent_summaries: list[str] = field(default_factory=list)
    pending_tasks: list[str] = field(default_factory=list)
    key_decisions: list[str] = field(default_factory=list)

    def note_file(self, path: str | None) -> None:
        if path and path not in self.files_changed:
            self.files_changed.append(path)

    def note_files(self, paths) -> None:
        for p in paths or []:
            self.note_file(p)

    def note_command(self, command: str | None) -> None:
        if command:
            self.commands_run.append(command)

    def note_error(self, message: str | None) -> None:
        if message:
            self.errors.append(message)

    def note_permission(self, text: str) -> None:
        self.permissions.append(text)

    def note_subagent(self, summary: str) -> None:
        self.subagent_summaries.append(summary)

    def preservation_summary(self) -> str:
        """A compact summary of everything that must survive compaction."""
        lines = [f"Goal: {self.goal}"]
        if self.plan:
            lines.append(f"Plan: {self.plan}")
        if self.files_changed:
            lines.append("Files changed: " + ", ".join(self.files_changed))
        if self.commands_run:
            lines.append("Commands run: " + "; ".join(self.commands_run))
        if self.errors:
            lines.append("Errors seen: " + " | ".join(self.errors[-5:]))
        if self.permissions:
            lines.append("Permission decisions: " + "; ".join(self.permissions[-8:]))
        if self.subagent_summaries:
            lines.append("Sub-agent results: " + " | ".join(self.subagent_summaries))
        if self.pending_tasks:
            lines.append("Pending: " + "; ".join(self.pending_tasks))
        return "\n".join(lines)
