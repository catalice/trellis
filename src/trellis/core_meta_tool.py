"""
Always-available tools — passed to the assembler regardless of domain routing.

Tools Claude always has access to:
  - update_current_context: record what's going on right now
  - save_preferences: save domain-specific coaching preferences
"""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from trellis.core_profile import CurrentContextService

_log = logging.getLogger(__name__)


# --- update_current_context -------------------------------------------------

UPDATE_CONTEXT_TOOL = {
    "name": "update_current_context",
    "description": (
        "Update current context — what's going on in the user's life right now "
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
                "enum": ["second_brain", "sense", "move", "learn"],
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
    context_service: CurrentContextService,
    preferences_repository,
) -> list[tuple[dict, callable]]:
    return [
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
