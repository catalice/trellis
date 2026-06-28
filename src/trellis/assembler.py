"""
Handles one conversation turn end to end.

Knows about: context layer ordering, domain routing, history, tool binding.
Does NOT know about: Claude API, specific domains, DB schemas.

To change context layer order or content: edit _build_context.
To add a domain: edit main.py only — nothing here changes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Protocol
from uuid import UUID

from trellis.oracle import Oracle
from trellis.registry import ContextLoader, TrellisRegistry
from trellis.router import Router

_log = logging.getLogger(__name__)

_HISTORY_TURNS = 10
_SUMMARISE_AFTER = 20

_SYSTEM_BASE = """\
You are Trellis — a personal coach and thinking partner.

You are honest, warm, and direct. You know the person you're coaching well \
from their profile. You adapt to their energy, not a fixed routine.

You have access to real data: body metrics, training, tasks, and life context. \
Use it. Don't ask for information you already have in context.

When they brain dump, help triage without judgment.
When they ask about training, use the actual plan and recent data.
When they need to capture something, do it immediately and confirm briefly.

Be brief unless depth is asked for. One clear thing at a time.

Data rules — always follow these:
- Retrieve before you summarise. If asked what's been saved, captured, or noted, \
call the relevant tool first. Never reconstruct from conversation memory — the DB \
is the source of truth.
- No duplicate saves. Before calling save_capture, check whether equivalent \
content was already saved in this conversation. If it was, skip the save and \
confirm what's already there.
"""


class _HistoryRepo(Protocol):
    def append(self, user_id: UUID, role: str, content: str) -> None: ...
    def recent(self, user_id: UUID, limit: int) -> list: ...
    def to_messages(self, turns: list) -> list[dict]: ...
    def domain_summary(self, user_id: UUID, domain: str) -> str | None: ...
    def turn_count(self, user_id: UUID) -> int: ...


class Assembler:
    def __init__(
        self,
        oracle: Oracle,
        registry: TrellisRegistry,
        history: _HistoryRepo,
        permanent: list[tuple[str, ContextLoader]],     # (label, loader) — always loaded, in order
        tracking_summary: tuple[str, ContextLoader],    # (label, loader) — always, brief
        intelligence: tuple[str, ContextLoader],        # (label, loader) — always, brief
        always_tools: list[tuple[dict, Callable]],      # always passed regardless of routing
        summarise_after: int = _SUMMARISE_AFTER,
        summariser: Callable | None = None,
        onboarding_check: Callable[[UUID], bool] | None = None,
        onboarding_system: str | None = None,
        onboarding_tools: list[tuple[dict, Callable]] | None = None,
    ) -> None:
        self._oracle = oracle
        self._registry = registry
        self._history = history
        self._permanent = permanent
        self._tracking_summary = tracking_summary
        self._intelligence = intelligence
        self._always_tools = always_tools
        self._summarise_after = summarise_after
        self._summariser = summariser
        self._onboarding_check = onboarding_check
        self._onboarding_system = onboarding_system
        self._onboarding_tools = onboarding_tools or []
        self._router = Router(registry.all_signals())
        self._last_summarised: dict[UUID, int] = {}

    def handle_turn(self, user_id: UUID, message: str) -> str:
        now = datetime.now(timezone.utc)

        if self._onboarding_check and self._onboarding_check(user_id):
            return self._handle_onboarding_turn(user_id, message, now)

        domains = self._router.route(message)
        _log.debug("routed %s → %s", message[:60], domains)

        context = self._build_context(user_id, now, domains)
        system = f"{_SYSTEM_BASE}\n\n---\n\n{context}"

        tool_schemas, bound_handlers = self._build_tools(user_id, now, domains)

        turns = self._history.recent(user_id, limit=_HISTORY_TURNS)
        messages = [
            *self._history.to_messages(turns),
            {"role": "user", "content": message},
        ]

        response = self._oracle.run(system, messages, tool_schemas, bound_handlers)

        self._history.append(user_id, "user", message)
        if response:
            self._history.append(user_id, "assistant", response)

        self._maybe_summarise(user_id, domains)

        return response

    # --- Onboarding mode ----------------------------------------------------

    def _handle_onboarding_turn(self, user_id: UUID, message: str, now: datetime) -> str:
        system = self._onboarding_system or _SYSTEM_BASE
        schemas = [schema for schema, _ in self._onboarding_tools]
        handlers = {
            schema["name"]: _bind(handler, user_id, now)
            for schema, handler in self._onboarding_tools
        }
        turns = self._history.recent(user_id, limit=_HISTORY_TURNS)
        messages = [
            *self._history.to_messages(turns),
            {"role": "user", "content": message},
        ]
        response = self._oracle.run(system, messages, schemas, handlers)
        self._history.append(user_id, "user", message)
        if response:
            self._history.append(user_id, "assistant", response)
        return response

    # --- Context assembly ---------------------------------------------------

    def _build_context(self, user_id: UUID, now: datetime, domains: set[str]) -> str:
        parts: list[str] = []

        parts.append(f"Today: {now.strftime('%A %d %B %Y, %H:%M')} (UTC)")

        for label, loader in self._permanent:
            result = self._safe_load(loader, user_id, now, label)
            if result:
                parts.append(result)

        t_label, t_loader = self._tracking_summary
        tracking = self._safe_load(t_loader, user_id, now, t_label)
        if tracking:
            parts.append(tracking)

        i_label, i_loader = self._intelligence
        intel = self._safe_load(i_loader, user_id, now, i_label)
        if intel:
            parts.append(intel)

        sorted_domains = sorted(domains)
        for domain in sorted_domains:
            ctx = self._safe_domain_context(domain, user_id, now)
            if ctx:
                parts.append(ctx)

        for domain in sorted_domains:
            summary = self._history.domain_summary(user_id, domain)
            if summary:
                parts.append(f"[{domain} conversation history]\n{summary}")

        return "\n\n---\n\n".join(parts)

    def _safe_domain_context(self, domain: str, user_id: UUID, now: datetime) -> str | None:
        try:
            return self._registry.load_context(domain, user_id, now)
        except Exception:
            _log.warning("domain context loader '%s' failed", domain, exc_info=True)
            return f"[{domain} data temporarily unavailable]"

    # --- Tool assembly ------------------------------------------------------

    def _build_tools(
        self, user_id: UUID, now: datetime, domains: set[str]
    ) -> tuple[list[dict], dict[str, Callable[[dict], str]]]:
        raw = list(self._always_tools) + self._registry.tools_for(domains)
        schemas = [schema for schema, _ in raw]
        handlers = {
            schema["name"]: _bind(handler, user_id, now)
            for schema, handler in raw
        }
        return schemas, handlers

    # --- Summarisation ------------------------------------------------------

    def _maybe_summarise(self, user_id: UUID, domains: set[str]) -> None:
        if self._summariser is None:
            return
        count = self._history.turn_count(user_id)
        last = self._last_summarised.get(user_id, 0)
        if count - last < self._summarise_after:
            return
        for domain in domains:
            try:
                self._summariser(user_id, domain, self._history)
            except Exception:
                _log.warning("summarisation failed for domain '%s'", domain, exc_info=True)
        self._last_summarised[user_id] = count

    # --- Helpers ------------------------------------------------------------

    @staticmethod
    def _safe_load(
        loader: ContextLoader, user_id: UUID, now: datetime, label: str
    ) -> str | None:
        try:
            return loader(user_id, now)
        except Exception:
            _log.warning("context loader '%s' failed", label, exc_info=True)
            return f"[{label} data temporarily unavailable]"


def _bind(handler: Callable, user_id: UUID, now: datetime) -> Callable[[dict], str]:
    def bound(input_dict: dict) -> str:
        return handler(user_id, input_dict, now)
    return bound
