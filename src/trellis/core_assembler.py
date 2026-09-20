"""
Handles one conversation turn end to end.

Knows about: context layer ordering, domain routing, history, tool binding.
Does NOT know about: any model provider's API, specific domains, DB schemas.

To change context layer order or content: edit _build_context.
To add a domain: edit main.py only — nothing here changes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol
from uuid import UUID

from trellis.core_actions import describe
from trellis.core_model import SystemPrompt
from trellis.core_oracle import Oracle
from trellis.core_registry import ContextLoader, TrellisRegistry
from trellis.core_router import Router
from trellis.infra_router import Embedder, SemanticRouter

_log = logging.getLogger(__name__)

_HISTORY_TURNS = 10           # onboarding only; the main path is time-based
_WINDOW_HOURS = 24            # verbatim memory = the last day (the user's design) ...
_WINDOW_CAP = 60              # ... capped so a wild day can't run away
_SUMMARISE_AFTER = 20

_SYSTEM_BASE = """\
You are Trellis: collaborator, coach, teacher, and the memory that holds what \
theirs can't. You carry the rules, the counts and the what-should-I-do-today \
load. Their profile and preferences say who they are and how to talk to them — \
follow them.

You hold real data — tasks, goals, captures, health, training, life context. \
Use it. Don't ask for what you have.

Listening
- Each message continues the conversation unless they clearly start something new.
- Answer all of it, in your words, before reporting what you did. Doing the \
task doesn't answer the question.
- Speak at the end of every turn. Tool results never reach them.
- They lead. Your questions are openings they can ignore.
- A thread you opened, see through.
- Unclear meaning: act on your best reading when it's easy to undo, and say \
what you did; ask first when a wrong guess costs them.

Reflexes
- Changes are agreements; records are yours to make. Before changing anything \
they rely on — plan, devices, task dates or status — decide, say it, get their \
yes. Capturing needs no permission.
- Stored state is not their life. After a gap, ask what happened before acting \
on what you hold.
- Handed something to hold: capture first, confirm briefly.
- Close when the thing is done. Not every reply ends with an offer or a question.

Telegram: plain text, short paragraphs, simple lists. No tables.

Honesty — non-negotiable
- Truthful beats helpful.
- "Done" means the tool was called and confirmed. Never claim a capability you lack.
- Never invent data. Never assert something exists or doesn't without \
retrieving it this turn — absence is an assertion too.
- Asked what's saved: retrieve, then summarise. The stores are the truth; \
history is a fallback.
- Health, medication, science: fetch a source before stating how something \
works. Recall is not a source.
- State every write in your reply. A question is never licence for silent changes.
- Before any write, check it exists; append or enrich, never duplicate or \
silently discard.
- Never send them to an earlier message. If it bears repeating, repeat it.
"""


def constitution_lines() -> list[str]:
    """The constitution's rules, one per item — so a preference that repeats
    one can be noticed (one home per rule)."""
    rules = _SYSTEM_BASE.split("\nListening\n", 1)[-1]
    return [ln.removeprefix("- ").strip() for ln in rules.splitlines() if len(ln.split()) > 3]


class _HistoryRepo(Protocol):
    def append(self, user_id: UUID, role: str, content: str, metadata: dict | None = None) -> None: ...
    def recent(self, user_id: UUID, limit: int) -> list: ...
    def recent_window(self, user_id: UUID, *, since, cap: int) -> list: ...
    def to_messages(self, turns: list) -> list[dict]: ...
    def domain_summary(self, user_id: UUID, domain: str) -> tuple[str, datetime] | None: ...
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
        intelligence: tuple[str, ContextLoader] | None = None,      # optional always-brief slot
        summarise_after: int = _SUMMARISE_AFTER,
        summariser: Callable | None = None,
        timezone=None,          # user tz: every model-facing clock renders local
        onboarding_check: Callable[[UUID], bool] | None = None,
        onboarding_system: str | None = None,
        onboarding_tools: list[tuple[dict, Callable]] | None = None,
        default_domain: str | None = None,
        embedder: Embedder | None = None,
        preferences=None,   # repo with .get(user_id, domain) -> str | None
        action_log: Callable[[UUID], object] | None = None,   # user_id -> a core_actions.ActionLog for one turn
    ) -> None:
        self._oracle = oracle
        self._registry = registry
        self._history = history
        self._permanent = permanent
        self._intelligence = intelligence
        self._always_tools = always_tools
        self._summarise_after = summarise_after
        self._summariser = summariser
        self._onboarding_check = onboarding_check
        self._onboarding_system = onboarding_system
        self._onboarding_tools = onboarding_tools or []
        self._preferences = preferences
        self._action_log = action_log
        self._timezone = timezone
        self._default_domain = default_domain
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
        # The constitution never changes; the context is this turn's. Said so,
        # a connector can cache the stable part (with the tool definitions).
        system = SystemPrompt(stable=_SYSTEM_BASE, volatile=context)

        tool_schemas, bound_handlers = self._build_tools(user_id, now, domains)

        turns = self._history.recent_window(
            user_id, since=now - timedelta(hours=_WINDOW_HOURS), cap=_WINDOW_CAP,
        )
        messages = [
            *self._history.to_messages(turns),
            {"role": "user", "content": message},
        ]
        # History is append-only within the day, so its prefix is stable —
        # mark where it ends and a connector can cache the whole prefix too.
        if len(messages) >= 2 and isinstance(messages[-2].get("content"), str):
            messages[-2] = {**messages[-2], "stable_prefix": True}

        # The user's message is persisted BEFORE the oracle runs: if the API
        # dies past its retries mid-turn, tool side effects from earlier
        # iterations have already committed — history must show the turn
        # happened, or the model (and the user) are told "nothing changed"
        # about a turn that changed things.
        self._history.append(user_id, "user", message, metadata={
            "handled_by": "claude",
            "domains": sorted(domains),
        })
        actions = self._action_log(user_id) if self._action_log is not None else None
        try:
            result = self._oracle.run(system, messages, tool_schemas, bound_handlers, actions=actions)
        except Exception:
            # The record outlives the model: say what the turn HAD done, from
            # the record, not a guess that something might have.
            on_record = list(getattr(actions, "entries", None) or [])
            self._history.append(
                user_id, "assistant",
                ("[turn failed mid-run — on record before the error: " + describe(on_record) + "]")
                if on_record else
                "[turn failed mid-run — no action was on record before the error]",
            )
            raise
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

        local_now = now.astimezone(self._timezone) if self._timezone else now
        parts.append(f"Today: {local_now.strftime('%A %d %B %Y, %H:%M')} (their local time)")

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
                text, written = summary
                day = written.astimezone(self._timezone).strftime("%-d %b") if self._timezone else written.strftime("%-d %b")
                parts.append(f"[{domain} — what was discussed, up to {day}]\n{text}")

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
            if schema["name"] in seen:
                # First registration wins — but never silently: a misconfigured
                # registry could otherwise hide a live tool without trace.
                _log.warning("duplicate tool name %r — later registration ignored",
                             schema["name"])
                continue
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
        # Big-brain turns route empty; summarise them under the default domain
        # so the cursor still advances — otherwise a run of generic chat
        # re-fires the prune every turn and no summary is ever written.
        if not domains and self._default_domain:
            domains = {self._default_domain}
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
