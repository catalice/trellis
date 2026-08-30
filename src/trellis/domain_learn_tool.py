"""
Tools for the Learn house — the surveyor's hands. The teaching happens in the
oracle turn (know-how in domain_learn_claude); these persist the map.

Handler signature: (user_id, input_dict, now) -> str
Context loader: learn_context_loader (Tier 1b — guidance + threads with positions)
Registration: learn_tools(...)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from trellis.domain_learn_claude import LEARN_GUIDANCE
from trellis.domain_learn_models import EntryKind
from trellis.domain_learn_service import SourceRequiredError

_log = logging.getLogger(__name__)

ContextLoader = Callable[[UUID, datetime], "str | None"]


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

LEARN_GET_TOOL: dict = {
    "name": "learn_get",
    "description": (
        "Read the user's knowledge maps. what='threads': every thread with its "
        "'you are here'. what='map': one thread's full map — regions, entries, "
        "sources, test history — read it before teaching or testing so you "
        "build on what's actually there."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {"type": "string", "enum": ["threads", "map"]},
            "thread": {"type": "string", "description": "map: the thread's title."},
        },
        "required": ["what"],
    },
}

LEARN_ADD_TOOL: dict = {
    "name": "learn_add",
    "description": (
        "Write to a knowledge map. what='thread': start a new topic they've "
        "chosen to build. what='entry': place a piece on the map — kind="
        "'material' (something learned, their words or your digest), kind="
        "'source' (a kept reference — source_url REQUIRED, fetched never "
        "recalled), kind='test' (a retrieval-practice outcome: question, their "
        "answer's gist, verdict). what='position': update 'you are here'. "
        "The region is THEIR label — ask where it fits; don't file it for them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {"type": "string", "enum": ["thread", "entry", "position"]},
            "thread": {"type": "string", "description": "The thread's title (created if what='thread', found otherwise)."},
            "kind": {
                "type": "string", "enum": ["material", "source", "test"],
                "description": "entry: what this piece is. Default material.",
            },
            "content": {"type": "string", "description": "entry: the piece itself. Required for entries."},
            "region": {"type": "string", "description": "entry: where THEY placed it on the map — their label, their call."},
            "source_url": {"type": "string", "description": "entry kind=source: the fetched URL. Required for sources."},
            "source_title": {"type": "string", "description": "entry kind=source: the source's name."},
            "position": {"type": "string", "description": "position: the new 'you are here', plain words."},
        },
        "required": ["what", "thread"],
    },
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_learn_get(user_id: UUID, input_dict: dict, now: datetime, *, learn_service) -> str:
    what = str(input_dict.get("what", "")).strip()
    if what == "threads":
        threads = learn_service.list_threads(user_id)
        if not threads:
            return "No threads yet — a thread starts when they choose a topic to build."
        lines = ["Threads (most recently touched first):"]
        for t in threads:
            pos = f" — you are here: {t.position}" if t.position else " — no position set yet"
            lines.append(f"  {t.title}{pos}")
        return "\n".join(lines)

    if what == "map":
        title = str(input_dict.get("thread", "")).strip()
        if not title:
            return "thread is required for what='map'."
        thread = next(
            (t for t in learn_service.list_threads(user_id)
             if t.title.lower() == title.lower()), None,
        )
        if thread is None:
            return f"No thread called '{title}'. learn_get what='threads' lists them."
        entries = learn_service.entries(user_id, thread)
        lines = [f"Map: {thread.title}"]
        if thread.position:
            lines.append(f"You are here: {thread.position}")
        if not entries:
            lines.append("Nothing placed yet.")
        for e in entries:
            src = f" [source: {e.source_title or e.source_url}]" if e.source_url else ""
            region = f"({e.region}) " if e.region else "(unplaced) "
            lines.append(f"  {region}{e.kind}: {e.content[:160]}{src}")
        return "\n".join(lines)

    return "Unknown what. Use: threads, map."


def handle_learn_add(user_id: UUID, input_dict: dict, now: datetime, *, learn_service) -> str:
    what = str(input_dict.get("what", "")).strip()
    title = str(input_dict.get("thread", "")).strip()
    if not title:
        return "thread is required."

    if what == "thread":
        thread = learn_service.find_or_create_thread(user_id, title, now)
        return f"Thread '{thread.title}' is open. Its map lives in the vault (Atlas/Maps)."

    thread = next(
        (t for t in learn_service.list_threads(user_id)
         if t.title.lower() == title.lower()), None,
    )
    if thread is None:
        return f"No thread called '{title}' — start it with what='thread' first."

    if what == "entry":
        content = str(input_dict.get("content", "")).strip()
        if not content:
            return "content is required for an entry."
        try:
            kind = EntryKind(str(input_dict.get("kind", "material")))
        except ValueError:
            kind = EntryKind.MATERIAL
        try:
            entry = learn_service.add_entry(
                user_id, thread, kind=kind, content=content,
                region=input_dict.get("region"),
                source_url=input_dict.get("source_url"),
                source_title=input_dict.get("source_title"),
                now=now,
            )
        except SourceRequiredError:
            return ("A kept reference must carry its source_url — fetch it "
                    "(web_search), don't recall it. Save as kind='material' only "
                    "if it's their own words, not a fact claim.")
        placed = f" in '{entry.region}'" if entry.region else " (unplaced — ask them where it fits)"
        return f"Placed on '{thread.title}'{placed}."

    if what == "position":
        position = str(input_dict.get("position", "")).strip()
        if not position:
            return "position is required."
        learn_service.set_position(user_id, thread, position)
        return f"'{thread.title}' — you are here: {position}"

    return "Unknown what. Use: thread, entry, position."


# ---------------------------------------------------------------------------
# Context loader (Tier 1b) + registration
# ---------------------------------------------------------------------------

def learn_context_loader(learn_service) -> ContextLoader:
    """Loaded when Learn is routed: the know-how + every thread's position, so
    teaching starts from where they actually are without a read call."""
    def loader(user_id: UUID, now: datetime) -> str | None:
        parts: list[str] = [LEARN_GUIDANCE]
        try:
            threads = learn_service.list_threads(user_id)
            if threads:
                lines = ["[Their threads]"]
                for t in threads:
                    pos = f" — you are here: {t.position}" if t.position else ""
                    lines.append(f"- {t.title}{pos}")
                parts.append("\n".join(lines))
        except Exception:
            _log.warning("learn context failed", exc_info=True)
        return "\n\n".join(parts)
    return loader


def learn_tools(learn_service) -> list[tuple[dict, Any]]:
    return [
        (LEARN_GET_TOOL,
         lambda uid, inp, now: handle_learn_get(uid, inp, now, learn_service=learn_service)),
        (LEARN_ADD_TOOL,
         lambda uid, inp, now: handle_learn_add(uid, inp, now, learn_service=learn_service)),
    ]


# Rooms — what this house handles, in phrases the router embeds.
LEARN_ROOMS = [
    "learning threads and knowledge maps",
    "explain from the foundations up",
    "how something works explained",
    "news and current events explained",
    "research findings and cited sources",
    "retrieval practice quiz",
]

# Keyword fallback (degraded mode only).
LEARN_SIGNALS = [
    "learn", "study", "understand", "explain", "teach",
    "thread", "map", "news", "source", "paper", "research",
]
