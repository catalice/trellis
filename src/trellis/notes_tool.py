"""
Tool schemas and handlers for the notes domain.

Covers capture retrieval. Saving captures (save_capture, list_captures) is in
meta_tools.py as always-available tools — not duplicated here.

Register with: registry.add_domain("notes", ..., notes_tools(...), NOTES_SIGNALS)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol
from uuid import UUID

_log = logging.getLogger(__name__)


# --- Protocols ---------------------------------------------------------------

class _CaptureRepo(Protocol):
    def list_recent(self, user_id: UUID, limit: int) -> list: ...
    def search_recent(self, user_id: UUID, reference: str, limit: int) -> list: ...


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

LIST_CAPTURES_INBOX_TOOL = {
    "name": "list_captures_inbox",
    "description": (
        "List recent captures from the inbox with their synthesis. "
        "Use when the user asks to review what they've captured, wants a summary of recent thoughts, "
        "or is doing an idea review."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

GET_CAPTURE_TOOL = {
    "name": "get_capture",
    "description": (
        "Search captures by keyword or topic. "
        "Use when the user refers to a specific thought, idea, or thing they captured."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reference": {
                "type": "string",
                "description": "Keyword or phrase to search for in captures.",
            },
        },
        "required": ["reference"],
    },
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_list_captures_inbox(
    user_id: UUID, input_dict: dict, now: datetime,
    *, capture_repository: _CaptureRepo,
) -> str:
    try:
        captures = capture_repository.list_recent(user_id, limit=20)
    except Exception:
        _log.warning("list_captures_inbox: failed to load", exc_info=True)
        return "Couldn't load captures right now — try again in a moment."

    if not captures:
        return "No captures yet."

    lines = [f"Captures ({len(captures)}):"]
    for c in captures:
        local = c.created_at.astimezone(now.tzinfo) if now.tzinfo else c.created_at
        lines.append(f"  [{local.strftime('%d %b %H:%M')}] {c.synthesis}")
    return "\n".join(lines)


def handle_get_capture(
    user_id: UUID, input_dict: dict, now: datetime,
    *, capture_repository: _CaptureRepo,
) -> str:
    reference = str(input_dict.get("reference", "")).strip()
    if not reference:
        return "No search reference provided."

    try:
        captures = capture_repository.search_recent(user_id, reference, limit=10)
    except Exception:
        _log.warning("get_capture: search failed", exc_info=True)
        return "Couldn't search captures right now — try again in a moment."

    if not captures:
        return f"No captures found matching '{reference}'."

    lines = [f"Found {len(captures)} match{'es' if len(captures) != 1 else ''}:"]
    for c in captures:
        local = c.created_at.astimezone(now.tzinfo) if now.tzinfo else c.created_at
        lines.append(f"  [{local.strftime('%d %b %H:%M')}] {c.synthesis}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

NOTES_SIGNALS: list[str] = [
    "note", "notes", "idea", "ideas", "inbox", "capture", "captures", "brain dump",
    "braindump", "think again", "book", "notebook", "incubator", "thought",
    "half idea", "something I was thinking", "jot down", "write down",
    "idea I had", "remember this", "keep this", "save this",
    "thing I want to explore", "filed", "store", "concept",
    "review captures", "review ideas", "what did I capture",
]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def notes_tools(
    capture_repository: _CaptureRepo,
) -> list[tuple[dict, callable]]:
    return [
        (
            LIST_CAPTURES_INBOX_TOOL,
            lambda uid, inp, now: handle_list_captures_inbox(
                uid, inp, now, capture_repository=capture_repository,
            ),
        ),
        (
            GET_CAPTURE_TOOL,
            lambda uid, inp, now: handle_get_capture(
                uid, inp, now, capture_repository=capture_repository,
            ),
        ),
    ]
