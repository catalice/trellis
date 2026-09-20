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

from trellis.core_actions import done, refused
from trellis.domain_learn_claude import LEARN_GUIDANCE
from trellis.domain_learn_models import EntryKind
from trellis.domain_learn_service import SourceRequiredError

_log = logging.getLogger(__name__)

_ENTRY_FRAGMENT = 160    # how much of a piece the map shows
_ENTRY_PAGE = 3000       # how much one page of a full read shows

ContextLoader = Callable[[UUID, datetime], "str | None"]


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

LEARN_GET_TOOL: dict = {
    "name": "learn_get",
    "description": (
        "Read a map before teaching or testing on it. Their positions are already "
        "in context; the map itself is not.\n"
        "threads: every thread and its 'you are here'.\n"
        "map: one thread — regions, pieces with ids, sources with URLs, test history. Long pieces are cut and say so.\n"
        "entry: one piece in full, a page at a time (thread, id, page) — read it before teaching from it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {"type": "string", "enum": ["threads", "map", "entry"]},
            "thread": {"type": "string", "description": "map, entry: the thread's title."},
            "id": {"type": "string", "description": "entry: its id, from the map."},
            "page": {"type": "integer", "description": "entry: which page, from 1. Omit for the first."},
        },
        "required": ["what"],
    },
}

LEARN_ADD_TOOL: dict = {
    "name": "learn_add",
    "description": (
        "Write to a map. A piece with no region lands unplaced — the receipt says "
        "so; the region is theirs to give.\n"
        "thread: open a topic they've chosen to build.\n"
        "entry: place a piece. material = learned, their words or your digest. "
        "source = a kept reference; refused without a fetched source_url. "
        "test = a retrieval outcome — the test itself is conversation, this is "
        "its one write.\n"
        "position: move 'you are here'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {"type": "string", "enum": ["thread", "entry", "position"]},
            "thread": {"type": "string", "description": "The thread's title. Created for thread, found otherwise."},
            "kind": {
                "type": "string", "enum": ["material", "source", "test"],
                "description": "entry: material | source | test. Default material.",
            },
            "content": {"type": "string", "description": "entry: the piece. Test: question, their gist, verdict."},
            "region": {"type": "string", "description": "entry: their label for where it sits."},
            "source_url": {"type": "string", "description": "entry kind=source: the fetched URL."},
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
            # A source ALWAYS shows its URL — a title alone can't be followed back.
            src = ""
            if e.source_url:
                src = f" [source: {e.source_title} — {e.source_url}]" if e.source_title else f" [source: {e.source_url}]"
            region = f"({e.region}) " if e.region else "(unplaced) "
            content = e.content.strip()
            if len(content) > _ENTRY_FRAGMENT:
                content = (f"{content[:_ENTRY_FRAGMENT]}… [cut — {len(content)} characters in all; "
                           f"learn_get what='entry' thread='{thread.title}' id='{e.id}' reads the whole of it]")
            lines.append(f"  [{e.id}] {region}{e.kind}: {content}{src}")
        return "\n".join(lines)

    if what == "entry":
        title = str(input_dict.get("thread", "")).strip()
        thread = next((t for t in learn_service.list_threads(user_id) if t.title.lower() == title.lower()), None)
        if thread is None:
            return f"No thread called '{title}'. learn_get what='threads' lists them."
        raw_id = str(input_dict.get("id", "")).strip()
        entry = next((e for e in learn_service.entries(user_id, thread) if str(e.id) == raw_id), None)
        if entry is None:
            return f"No entry with id {raw_id!r} on '{thread.title}' — what='map' lists them with their ids."
        body = entry.content.strip()
        pages = max(1, -(-len(body) // _ENTRY_PAGE))
        try:
            page = int(input_dict.get("page") or 1)
        except (TypeError, ValueError):
            page = 1
        if page < 1 or page > pages:
            return f"That entry has {pages} page(s) — ask for page 1 to {pages}."
        head = f"{thread.title} — {entry.kind}" + (f" in '{entry.region}'" if entry.region else " (unplaced)")
        if entry.source_url:
            head += f" — source: {entry.source_title or ''} {entry.source_url}".rstrip()
        if pages > 1:
            head += f" (page {page} of {pages}; pass page= for the others)"
        return f"{head}\n{body[(page - 1) * _ENTRY_PAGE: page * _ENTRY_PAGE]}"

    return "Unknown what. Use: threads, map, entry."


def handle_learn_add(user_id: UUID, input_dict: dict, now: datetime, *, learn_service) -> str:
    what = str(input_dict.get("what", "")).strip()
    title = str(input_dict.get("thread", "")).strip()
    if not title:
        return refused("thread is required.")

    if what == "thread":
        thread = learn_service.find_or_create_thread(user_id, title, now)
        return done(f"Thread '{thread.title}' is open. Its map lives in the vault (Atlas/Maps).")

    thread = next(
        (t for t in learn_service.list_threads(user_id)
         if t.title.lower() == title.lower()), None,
    )
    if thread is None:
        return refused(f"No thread called '{title}' — start it with what='thread' first.")

    if what == "entry":
        content = str(input_dict.get("content", "")).strip()
        if not content:
            return refused("content is required for an entry.")
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
            return refused("Refused: a kept reference needs its fetched source_url. "
                    "Their own words can go as material; a recalled fact can't.")
        placed = f" in '{entry.region}'" if entry.region else " (unplaced — ask them where it fits)"
        return done(f"Placed on '{thread.title}'{placed}.")

    if what == "position":
        position = str(input_dict.get("position", "")).strip()
        if not position:
            return refused("position is required.")
        learn_service.set_position(user_id, thread, position)
        return done(f"'{thread.title}' — you are here: {position}")

    return refused("Unknown what. Use: thread, entry, position.")


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
