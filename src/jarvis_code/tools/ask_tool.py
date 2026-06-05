"""ask_user: a sparing way to get a clarification from the user mid-run.

Asking mutates nothing, so it is allowed in every mode. Two guardrails keep the
agent from over-asking: a per-turn cap (``ToolContext.max_questions``) and a tool
description + system-prompt guidance that say to ask only when genuinely blocked.
When no interactive user is available the tool degrades to "proceed with your
best assumption" rather than stalling.
"""

from __future__ import annotations

from jarvis_code.permissions.modes import Capability

from .base import Tool, ToolContext, ToolResult

_PROCEED = "Proceed with your best assumption and state it explicitly."


class AskUserTool(Tool):
    name = "ask_user"
    description = (
        "Ask the user ONE short, specific clarifying question and get their answer. "
        "Use this ONLY when genuinely blocked by an ambiguity you cannot resolve "
        "yourself — a real fork in the requirements where guessing wrong wastes "
        "real work. Never ask about things you can determine by reading the code, "
        "and never ask for confirmation of a reasonable default. Use sparingly."
    )
    capability = Capability.ASK
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "A brief, specific question (one sentence).",
            },
        },
        "required": ["question"],
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        question = str(self._require(args, "question")).strip()
        if not question:
            return ToolResult.fail("empty question")
        if ctx.asker is None:
            return ToolResult.success(
                f"No interactive user is available to answer. {_PROCEED}", answered=False
            )
        if ctx.questions_asked >= ctx.max_questions:
            return ToolResult.success(
                "You've already asked the user enough for this task; do not ask "
                f"again. {_PROCEED}",
                answered=False,
            )
        ctx.questions_asked += 1
        answer = ctx.asker(question)
        if not answer or not str(answer).strip():
            return ToolResult.success(
                f"The user did not answer. {_PROCEED}", answered=False
            )
        return ToolResult.success(str(answer).strip(), answered=True)
