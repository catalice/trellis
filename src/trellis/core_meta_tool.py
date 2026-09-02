"""
Always-available tools — passed to the assembler regardless of domain routing.

Tools Claude always has access to:
  - update_current_context: record what's going on right now
  - save_preferences: save standing preferences — global (every turn) or per-domain
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
        "The user's standing preference RULES — one rule per row, each with an "
        "id. action='add': save a new rule (domain='global' applies every turn; "
        "a house domain loads with that house). action='list': every rule with "
        "its id — read before updating or removing. action='update'/'remove': "
        "change or delete ONE rule by rule_id. Rules also appear in the vault "
        "(Atlas/Brain/Preferences.md) where the user reviews them."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "update", "remove"],
                "default": "add",
            },
            "domain": {
                "type": "string",
                "enum": ["global", "focus", "sense", "move", "learn"],
                "description": "add: where the rule applies. 'global' when in doubt.",
            },
            "text": {
                "type": "string",
                "description": (
                    "add/update: the rule, second person, one sentence or two — "
                    "e.g. 'You never use tables; they don't render in Telegram.'"
                ),
            },
            "rule_id": {"type": "string", "description": "update/remove: the rule's id (from action='list')."},
        },
        "required": ["action"],
    },
}


def handle_save_preferences(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    preferences_repository,
    brain_changed=None,   # () -> None: refresh the vault's Brain pages
) -> str:
    action = str(input_dict.get("action", "add")).strip()
    text = str(input_dict.get("text", "")).strip()

    def _refresh():
        if brain_changed is not None:
            try:
                brain_changed()
            except Exception:
                _log.warning("brain page refresh failed", exc_info=True)

    try:
        if action == "list":
            rules = preferences_repository.list_rules(user_id)
            if not rules:
                return "No preference rules saved yet."
            lines = ["Preference rules:"]
            for r in rules:
                lines.append(f"  [{r['id']}] ({r['domain']}) {r['rule']}")
            return "\n".join(lines)

        if action == "add":
            domain = str(input_dict.get("domain", "")).strip() or "global"
            if not text:
                return "text is required to add a rule."
            preferences_repository.add_rule(user_id, domain, text)
            _refresh()
            return f"Rule saved ({domain})."

        if action == "update":
            rid = str(input_dict.get("rule_id", "")).strip()
            if not rid or not text:
                return "rule_id and text are required to update."
            if not preferences_repository.update_rule(user_id, UUID(rid), text):
                return "No rule with that id — action='list' shows them."
            _refresh()
            return "Rule updated."

        if action == "remove":
            rid = str(input_dict.get("rule_id", "")).strip()
            if not rid:
                return "rule_id is required to remove."
            if not preferences_repository.remove_rule(user_id, UUID(rid)):
                return "No rule with that id — action='list' shows them."
            _refresh()
            return "Rule removed."

        return "Unknown action. Use: add, list, update, remove."
    except ValueError:
        return "That rule_id isn't a valid id."
    except Exception:
        _log.exception("save_preferences failed for user %s", user_id)
        return "Couldn't save that — try again in a moment."

def meta_tools(
    context_service: CurrentContextService,
    preferences_repository,
    brain_changed=None,   # (user_id) -> None: refresh the vault's Brain pages
) -> list[tuple[dict, callable]]:
    def _refresh(uid):
        if brain_changed is not None:
            try:
                brain_changed(uid)
            except Exception:
                _log.warning("brain page refresh failed", exc_info=True)

    def _context(uid, inp, now):
        out = handle_update_current_context(uid, inp, now, context_service=context_service)
        _refresh(uid)
        return out

    def _prefs(uid, inp, now):
        return handle_save_preferences(
            uid, inp, now, preferences_repository=preferences_repository,
            brain_changed=lambda: _refresh(uid),
        )

    return [
        (UPDATE_CONTEXT_TOOL, _context),
        (SAVE_PREFERENCES_TOOL, _prefs),
    ]
