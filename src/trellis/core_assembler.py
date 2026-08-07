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

from trellis.core_oracle import Oracle
from trellis.core_registry import ContextLoader, TrellisRegistry
from trellis.core_router import Router
from trellis.infra_router import Embedder, SemanticRouter

_log = logging.getLogger(__name__)

_HISTORY_TURNS = 10
_SUMMARISE_AFTER = 20

_SYSTEM_BASE = """\
You are Trellis — their chosen structure. A trellis is what something living \
grows on: that's you. You're the steady buddy who keeps them on track, the mind \
that holds what theirs can't, the sorting hand that closes open loops and tabs \
so they don't have to keep them open. You carry the "what should I do today" \
load so their working memory doesn't. You help them interpret what's swirling — \
and you back their growth wherever it's happening: training the body, steadying \
the mind, learning, building, becoming more themselves. You open nothing you \
don't intend to close.

You are not a chatbot performing helpfulness; you are a quiet, reliable \
extension of how they think and live. Honest, warm, and direct. You know them \
from their profile and adapt to how their mind works, not a fixed routine.

You have access to real data: their tasks, goals, captures, health and \
training, life context. Use it. Don't ask for what you already have.

How to listen:
- Understand what they mean before deciding what to do. You're in an ongoing \
conversation: what they say continues it unless they clearly start something \
new. Hear each message as part of what you're working on together.
- Answer ALL of it. One message often carries several things — a task and a \
question, two questions, a feeling and a favour. Address every part. Doing \
the task does not answer the question.
- They lead. Your questions and suggestions are openings — their reply can take \
one up, ignore it, or go somewhere else entirely. Follow them, not your own \
agenda.
- When meaning is unclear: act on your best understanding when it's easy to \
undo, and say plainly what you did. Ask first when getting it wrong would cost \
them. Never act on a guess without saying so.
- Speak at the end of every turn. Tool results never reach them — your words \
are the only thing they hear.

Reflexes:
- When they hand you something to hold — a dump, a thought, a thing to \
remember — capture it before it's lost, then confirm briefly.
- Don't end every reply with an offer or a question; close when the thing is \
done.

Be brief unless depth is asked for. One clear thing at a time. You speak \
through Telegram: plain text, short paragraphs, simple lists. Never tables — \
they don't render there. No heavy markdown.

Honesty — this is non-negotiable:
- Being truthful is the most important form of being helpful.
- Never claim to have done something without calling the tool. "Done" means \
the tool was called and confirmed. If you didn't call it, say so.
- Never claim a capability you don't have. If you're unsure, say you're unsure.
- Never invent data. If something isn't in your context or returned by a tool, \
say you don't know.
- Retrieve before you summarise. If asked what's been saved, call the relevant \
tool first. Conversation history is a fallback only — the DB is the source of truth.
- Never assert that something does or doesn't exist without retrieving it this turn.
- Every write you make — create, complete, delete, update — must be stated in \
your reply, even when it was catching up on an earlier instruction. A question \
is never licence for silent changes: if answering it revealed cleanup to do, \
say what you did.
- Before any write — capture, task, goal, anchor, preference, learning entry — \
check whether it already exists. If it does, append or enrich rather than \
duplicate or overwrite. Never silently discard existing content.
"""


class _HistoryRepo(Protocol):
    def append(self, user_id: UUID, role: str, content: str, metadata: dict | None = None) -> None: ...
    def recent(self, user_id: UUID, limit: int) -> list: ...
    def to_messages(self, turns: list) -> list[dict]: ...
    def domain_summary(self, user_id: UUID, domain: str) -> str | None: ...
    def turn_count(self, user_id: UUID) -> int: ...
    def max_turns_covered(self, user_id: UUID) -> int: ...
    def prune(self, user_id: UUID, keep: int = 50) -> None: ...


class Assembler:
    def __init__(
        self,
        oracle: Oracle,
        registry: TrellisRegistry,
        history: _HistoryRepo,
        permanent: list[tuple[str, ContextLoader]],     # (label, loader) — always loaded, in order
        always_tools: list[tuple[dict, Callable]],      # always passed regardless of routing
        tracking_summary: tuple[str, ContextLoader] | None = None,  # optional always-brief slot
        intelligence: tuple[str, ContextLoader] | None = None,      # optional always-brief slot
        summarise_after: int = _SUMMARISE_AFTER,
        summariser: Callable | None = None,
        onboarding_check: Callable[[UUID], bool] | None = None,
        onboarding_system: str | None = None,
        onboarding_tools: list[tuple[dict, Callable]] | None = None,
        default_domain: str | None = None,
        embedder: Embedder | None = None,
        preferences=None,   # repo with .get(user_id, domain) -> str | None
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
        self._preferences = preferences
        # Routing shapes CONTEXT only (tools are always available). Semantic when
        # an embedder is wired — each domain is a house scored by the best-matching
        # room inside it; empty match -> no house, the big brain (permanent
        # context) carries the turn. The keyword router is the graceful fallback
        # if the embedder is down, so routing can never take the bot out.
        keyword_router = Router(registry.all_signals(), default_domain=default_domain)
        houses = registry.all_rooms()
        if embedder is not None and houses:
            self._router: Router | SemanticRouter = SemanticRouter(
                houses, embedder, fallback=keyword_router,
            )
        else:
            self._router = keyword_router

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

        result = self._oracle.run(system, messages, tool_schemas, bound_handlers)

        self._history.append(user_id, "user", message, metadata={
            "handled_by": "claude",
            "domains": sorted(domains),
        })
        self._save_assistant_turn(user_id, result)

        self._maybe_summarise(user_id, domains)

        return result.text

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
        result = self._oracle.run(system, messages, schemas, handlers)
        self._history.append(user_id, "user", message)
        self._save_assistant_turn(user_id, result)
        return result.text

    # --- Context assembly ---------------------------------------------------

    def _build_context(self, user_id: UUID, now: datetime, domains: set[str]) -> str:
        parts: list[str] = []

        parts.append(f"Today: {now.strftime('%A %d %B %Y, %H:%M')} (UTC)")

        for label, loader in self._permanent:
            result = self._safe_load(loader, user_id, now, label)
            if result:
                parts.append(result)

        # Their standing preferences — the user-editable layer on top of the
        # system prompt. Global ones load every turn (saved via save_preferences
        # with domain="global"); house ones load with their house below.
        global_prefs = self._safe_preferences(user_id, "global")
        if global_prefs:
            parts.append(f"[Their standing preferences — always apply]\n{global_prefs}")

        if self._tracking_summary is not None:
            t_label, t_loader = self._tracking_summary
            tracking = self._safe_load(t_loader, user_id, now, t_label)
            if tracking:
                parts.append(tracking)

        if self._intelligence is not None:
            i_label, i_loader = self._intelligence
            intel = self._safe_load(i_loader, user_id, now, i_label)
            if intel:
                parts.append(intel)

        sorted_domains = sorted(domains)
        for domain in sorted_domains:
            ctx = self._safe_domain_context(domain, user_id, now)
            if ctx:
                parts.append(ctx)
            prefs = self._safe_preferences(user_id, domain)
            if prefs:
                parts.append(f"[Their {domain} preferences]\n{prefs}")

        for domain in sorted_domains:
            summary = self._history.domain_summary(user_id, domain)
            if summary:
                parts.append(f"[{domain} conversation history]\n{summary}")

        return "\n\n---\n\n".join(parts)

    def _safe_preferences(self, user_id: UUID, domain: str) -> str | None:
        if self._preferences is None:
            return None
        try:
            return self._preferences.get(user_id, domain)
        except Exception:
            _log.warning("preferences load failed for '%s'", domain, exc_info=True)
            return None

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
        seen: set[str] = set()
        raw: list[tuple[dict, Callable]] = []
        # ALL domain tools are always available — the model decides what to call
        # from the tool descriptions. Keyword routing (the `domains` arg) shapes
        # CONTEXT only, never which tools exist. This stops the model denying a
        # capability (e.g. Garmin) just because the message missed a keyword.
        for schema, handler in list(self._always_tools) + self._registry.all_tools():
            if schema["name"] not in seen:
                seen.add(schema["name"])
                raw.append((schema, handler))
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
        # The cursor lives in the DB (turns_covered at last summarisation), so a
        # restart doesn't reset it and re-fire the summariser on the first turn.
        last = self._history.max_turns_covered(user_id)
        if count - last < self._summarise_after:
            return
        # Prune BEFORE summarising: the summariser stores the current turn_count
        # as the cursor, so pruning must happen first or the stored cursor sits
        # above the post-prune count and silently doubles the interval. 500 kept
        # turns ≫ the 10-turn context window + 40-turn summary window.
        try:
            self._history.prune(user_id, keep=500)
        except Exception:
            _log.warning("history prune failed", exc_info=True)
        for domain in domains:
            try:
                self._summariser(user_id, domain, self._history)
            except Exception:
                _log.warning("summarisation failed for domain '%s'", domain, exc_info=True)

    # --- Helpers ------------------------------------------------------------

    def _save_assistant_turn(self, user_id: UUID, result) -> None:
        """Persist the assistant's reply with a trace of tool calls made.

        The trace goes into history only (not the user-facing reply) so that
        future turns can see which actions were actually taken — without it,
        Claude reads a text-only transcript and can't tell whether "done"
        meant a real tool call.
        """
        if not result.text and not result.tool_calls:
            return
        content = result.text
        trace = result.trace()
        if trace:
            content = f"{content}\n{trace}" if content else trace
        self._history.append(user_id, "assistant", content)

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
