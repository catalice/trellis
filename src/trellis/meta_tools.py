"""
Always-available tools — passed to the assembler regardless of domain routing.

Tools Claude always has access to:
  - save_capture: synthesise + persist a brain dump / thought
  - list_captures: show recent captures
  - update_current_context: record what's going on right now
"""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from trellis.obsidian import ObsidianRawCaptureProjection
from trellis.user_context import CurrentContextService

_log = logging.getLogger(__name__)


# --- save_capture -----------------------------------------------------------

SAVE_CAPTURE_TOOL = {
    "name": "save_capture",
    "description": (
        "Save a synthesised capture — a thought, connection, or brain dump — to the captures inbox. "
        "Always synthesise the raw input first, then call this tool. "
        "The raw text is stored for reference; the synthesis is what shows in Obsidian and context."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "raw": {
                "type": "string",
                "description": "The original unedited text from the user.",
            },
            "synthesis": {
                "type": "string",
                "description": (
                    "Your synthesised version — clear, useful, written for future Cat to read back. "
                    "Distil the key insight, connection, or idea from the raw input."
                ),
            },
        },
        "required": ["raw", "synthesis"],
    },
}


def handle_save_capture(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    capture_repository,
    capture_projection: ObsidianRawCaptureProjection | None = None,
) -> str:
    raw = str(input_dict.get("raw", "")).strip()
    synthesis = str(input_dict.get("synthesis", "")).strip()
    if not raw or not synthesis:
        return "Both raw and synthesis are required."
    try:
        capture_repository.save(user_id, raw, synthesis)
    except Exception:
        _log.exception("save_capture failed for user %s", user_id)
        return "Couldn't save that capture — try again in a moment."
    if capture_projection is not None:
        try:
            capture_projection.append(synthesis, now)
        except Exception:
            _log.warning("save_capture: obsidian write failed", exc_info=True)
    return "Captured."


# --- list_captures ----------------------------------------------------------

LIST_CAPTURES_TOOL = {
    "name": "list_captures",
    "description": (
        "List recent captures with their synthesis. "
        "Use when the user asks what they've captured recently, wants to review thoughts, "
        "or asks to see their capture inbox."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def handle_list_captures(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    capture_repository,
) -> str:
    try:
        captures = capture_repository.list_recent(user_id, limit=20)
    except Exception:
        _log.exception("list_captures failed for user %s", user_id)
        return "Couldn't load captures right now — try again in a moment."
    if not captures:
        return "No captures yet."
    lines = [f"Recent captures ({len(captures)}):"]
    for c in captures:
        local = c.created_at.astimezone(now.tzinfo) if now.tzinfo else c.created_at
        lines.append(f"  [{local.strftime('%d %b %H:%M')}] {c.synthesis}")
    return "\n".join(lines)


# --- update_current_context -------------------------------------------------

UPDATE_CONTEXT_TOOL = {
    "name": "update_current_context",
    "description": (
        "Update current context — what's going on in Cat's life right now "
        "that Trellis should know about. Use any combination of fields."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "context": {
                "type": "string",
                "description": "General notes about what's going on right now.",
            },
            "physical_notes": {
                "type": "string",
                "description": "Physical state — injuries, illness, energy, body feels.",
            },
            "cognitive_notes": {
                "type": "string",
                "description": "Cognitive/exec state — stress, focus, life load, overwhelm.",
            },
        },
        "required": [],
    },
}


def handle_update_current_context(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    context_service: CurrentContextService,
) -> str:
    context_text = str(input_dict.get("context", "")).strip() or None
    physical_notes = str(input_dict.get("physical_notes", "")).strip() or None
    cognitive_notes = str(input_dict.get("cognitive_notes", "")).strip() or None

    if not any([context_text, physical_notes, cognitive_notes]):
        return "Nothing to update."
    try:
        context_service.update(
            user_id,
            misc_notes=context_text,
            physical_notes=physical_notes,
            cognitive_notes=cognitive_notes,
            today=now.date(),
        )
        return "Got it."
    except Exception:
        _log.exception("update_current_context failed for user %s", user_id)
        return "Couldn't save that — try again in a moment."


# --- save_preferences -------------------------------------------------------

SAVE_PREFERENCES_TOOL = {
    "name": "save_preferences",
    "description": (
        "Save the user's stated preferences for a domain. Call when the user expresses "
        "how they want to be coached, taught, or supported in a specific area — e.g. "
        "'I want to learn from the scaffold up', 'don't give me long plans', "
        "'I prefer shorter sessions'. These preferences load automatically whenever "
        "that domain is active."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "domain": {
                "type": "string",
                "enum": ["training", "learn", "ef", "notes"],
                "description": "Which domain these preferences apply to.",
            },
            "content": {
                "type": "string",
                "description": (
                    "The user's preferences in plain text. Write in second person "
                    "as a reminder to future Trellis — e.g. 'You prefer teaching "
                    "from the big picture down, not facts first.'"
                ),
            },
        },
        "required": ["domain", "content"],
    },
}


def handle_save_preferences(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    preferences_repository,
) -> str:
    domain = input_dict.get("domain", "").strip()
    content = input_dict.get("content", "").strip()
    if not domain or not content:
        return "Domain and content are required."
    try:
        preferences_repository.set(user_id, domain, content)
        return f"Preferences saved for {domain}."
    except Exception:
        _log.exception("save_preferences failed for user %s", user_id)
        return "Couldn't save preferences — try again in a moment."


# --- Registration ------------------------------------------------------------

def meta_tools(
    capture_repository,
    context_service: CurrentContextService,
    preferences_repository,
    capture_projection: ObsidianRawCaptureProjection | None = None,
) -> list[tuple[dict, callable]]:
    return [
        (
            SAVE_CAPTURE_TOOL,
            lambda uid, inp, now: handle_save_capture(
                uid, inp, now,
                capture_repository=capture_repository,
                capture_projection=capture_projection,
            ),
        ),
        (
            LIST_CAPTURES_TOOL,
            lambda uid, inp, now: handle_list_captures(
                uid, inp, now,
                capture_repository=capture_repository,
            ),
        ),
        (
            UPDATE_CONTEXT_TOOL,
            lambda uid, inp, now: handle_update_current_context(
                uid, inp, now, context_service=context_service,
            ),
        ),
        (
            SAVE_PREFERENCES_TOOL,
            lambda uid, inp, now: handle_save_preferences(
                uid, inp, now, preferences_repository=preferences_repository,
            ),
        ),
    ]
