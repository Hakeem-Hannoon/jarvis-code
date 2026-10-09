"""Change a running session's model, provider, effort or mode.

A runtime is assembled from several pieces that each bake a setting in: the
provider (model + response budget), the tool registry (sub-agents exist only at
``madmax``), the system prompt (mode and effort guidance), and the sub-agent
orchestrator (the permission ceiling sub-agents are clamped to). Switching a
setting mid-session therefore means updating *all* of the pieces that hold it —
otherwise the model keeps being told it is in the old mode, or sub-agents keep
running under a ceiling the user just lowered.

:class:`SessionControls` is that single place. It is attached to the runtime as
``runtime.controls`` by ``assemble_runtime``; the CLI's ``/model``, ``/provider``,
``/effort`` and ``/mode`` commands are thin wrappers over it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from jarvis_code.config import ConfigValidationError, Effort, JarvisConfig
from jarvis_code.models import build_provider
from jarvis_code.permissions import PermissionMode
from jarvis_code.tools import build_default_registry
from jarvis_code.utils.logging import redact

from .effort import profile_for
from .prompts import build_system_prompt

if TYPE_CHECKING:  # pragma: no cover
    from .loop import AgentRuntime


@dataclass(frozen=True)
class SessionSettings:
    """What the session is running with right now."""

    provider: str
    model: str
    effort: Effort
    mode: PermissionMode


@dataclass(frozen=True)
class ProviderInfo:
    """One configured provider, as shown in a picker (never includes the key)."""

    name: str
    model: str  # its default model
    has_key: bool
    current: bool


def split_model_spec(spec: str, providers: list[str] | set[str]) -> tuple[str | None, str]:
    """Split ``"openai/gpt-5"`` into ``("openai", "gpt-5")``.

    Only a prefix that names a *configured provider* is treated as one, so model
    ids that themselves contain a slash (``meta/llama-4``) pass through whole.
    """
    spec = spec.strip()
    head, sep, tail = spec.partition("/")
    if sep and tail and head in providers:
        return head, tail
    return None, spec


class SessionControls:
    def __init__(
        self,
        runtime: "AgentRuntime",
        *,
        config: JarvisConfig,
        provider_name: str,
        effort: Effort,
        memory: str | None,
        narrate: bool,
        can_ask: bool,
        http_client: Any = None,
    ) -> None:
        self.runtime = runtime
        self.config = config
        self._provider_name = provider_name
        self._effort = effort
        self._memory = memory
        self._narrate = narrate
        self._can_ask = can_ask
        self._http_client = http_client
        # provider name -> model ids fetched from its API this session.
        self._listed: dict[str, list[str]] = {}

    # -- reading -----------------------------------------------------------

    @property
    def settings(self) -> SessionSettings:
        return SessionSettings(
            provider=self._provider_name,
            model=getattr(self.runtime.provider, "model", ""),
            effort=self._effort,
            mode=self.runtime.executor.permissions.mode,
        )

    def providers(self) -> list[ProviderInfo]:
        """Every configured provider, current one first."""
        infos = [
            ProviderInfo(
                name=name,
                model=pconf.model,
                has_key=pconf.has_key(),
                current=name == self._provider_name,
            )
            for name, pconf in self.config.providers.items()
        ]
        return sorted(infos, key=lambda i: (not i.current, i.name))

    def known_models(self, provider: str | None = None) -> list[str]:
        """Models we can offer for ``provider`` WITHOUT a network call.

        Its configured default, the ``models = [...]`` favourites from the
        config, the model in use, and anything already fetched this session.
        """
        name = provider or self._provider_name
        pconf = self.config.providers.get(name)
        out: list[str] = []
        if name == self._provider_name:
            out.append(self.settings.model)
        if pconf is not None:
            out += [pconf.model, *pconf.models]
        out += self._listed.get(name, [])
        return list(dict.fromkeys(m for m in out if m))  # de-duplicated, ordered

    def fetch_models(self, provider: str | None = None) -> list[str]:
        """Ask the provider's API which models exist (cached; ``[]`` if it can't say)."""
        name = provider or self._provider_name
        if name not in self._listed:
            try:
                client = (
                    self.runtime.provider
                    if name == self._provider_name
                    else build_provider(self.config, provider=name, http_client=self._http_client)
                )
                lister = getattr(client, "list_models", None)
                self._listed[name] = lister() if lister is not None else []
            except Exception:  # no key for that provider, bad endpoint, …
                self._listed[name] = []
        return self._listed[name]

    # -- switching ---------------------------------------------------------

    def set_model(self, model: str, provider: str | None = None) -> SessionSettings:
        """Switch model (and provider, if given or written as ``provider/model``).

        Raises a ``ConfigError`` (unknown provider, missing key, missing endpoint)
        *before* anything changes, so a failed switch leaves the session as it was.
        """
        if provider is None:
            provider, model = split_model_spec(model, set(self.config.providers))
        name = provider or self._provider_name
        model = model.strip()
        if not model:
            raise ConfigValidationError("No model name given.")
        new_provider = build_provider(
            self.config,
            provider=name,
            model=model,
            http_client=self._http_client,
            max_tokens=profile_for(self._effort).max_tokens,
        )
        self._provider_name = name
        self.runtime.provider = new_provider
        orchestrator = self.runtime.executor.ctx.orchestrator
        if orchestrator is not None:
            orchestrator.provider = new_provider  # sub-agents follow the switch
        return self.settings

    def set_provider(self, provider: str) -> SessionSettings:
        """Switch to ``provider`` using the model configured for it."""
        pconf = self.config.providers.get(provider)
        if pconf is None:
            known = ", ".join(sorted(self.config.providers)) or "(none)"
            raise ConfigValidationError(
                f"Provider '{provider}' is not configured. Configured: {known}"
            )
        return self.set_model(pconf.model, provider)

    def set_effort(self, effort: Effort) -> SessionSettings:
        """Switch effort: step ceiling, response budget, sub-agents, prompt nudge."""
        profile = profile_for(effort)
        self._effort = effort
        self.runtime.max_steps = profile.max_steps
        if hasattr(self.runtime.provider, "max_tokens"):
            self.runtime.provider.max_tokens = profile.max_tokens
        # Sub-agents exist only at madmax: add/remove the tool to match.
        self.runtime.executor.registry = build_default_registry(
            include_subagents=profile.use_subagents, include_ask=self._can_ask
        )
        self._rebuild_prompt()
        return self.settings

    def set_mode(self, mode: PermissionMode) -> SessionSettings:
        """Switch permission mode everywhere that enforces or describes it."""
        self.runtime.executor.permissions.mode = mode
        orchestrator = self.runtime.executor.ctx.orchestrator
        if orchestrator is not None:
            # The ceiling sub-agents are clamped to. Left stale, a session
            # lowered from dangerous to plan kept spawning dangerous sub-agents.
            orchestrator.parent_mode = mode
        self._rebuild_prompt()
        return self.settings

    def _rebuild_prompt(self) -> None:
        """Re-render the system prompt so it describes the current settings."""
        runtime = self.runtime
        prompt = build_system_prompt(
            mode=runtime.executor.permissions.mode,
            sandbox_root=str(runtime.executor.ctx.sandbox.root),
            tool_specs=runtime.executor.registry.specs(),
            memory=self._memory,
            effort_note=profile_for(self._effort).guidance,
            narrate=self._narrate,
            can_ask=self._can_ask,
        )
        runtime.context.system_prompt = redact(prompt)
