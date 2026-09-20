"""
Always-available tools — passed to the assembler regardless of domain routing.

Tools Claude always has access to:
  - update_current_context: record what's going on right now
  - save_preferences: save standing preferences — global (every turn) or per-domain
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from uuid import UUID

from trellis.core_actions import done, failed, refused, unknown
from trellis.core_profile import AtCap, CurrentContextService, LineGuard, TooLong

_log = logging.getLogger(__name__)


# --- update_current_context -------------------------------------------------

UPDATE_CONTEXT_TOOL = {
    "name": "update_current_context",
    "description": (
        "Their life right now, as dated one-liners: decisions and facts no store "
        "holds. add when they decide or tell you something that changes the "
        "picture. Their words, no interpretation. Over 10 words is refused. "
        "Lines lapse after 14 days unless until is set."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["add", "remove"], "default": "add"},
            "text": {"type": "string", "description": "add: the line, 10 words max. remove: words from the line to drop."},
            "until": {"type": "string", "description": "add: YYYY-MM-DD it stays true until. Optional."},
        },
        "required": ["text"],
    },
}


def _too_long(exc: TooLong) -> str:
    return refused(f"Not saved — {exc.words} words, the limit is {exc.limit}. "
                   "One line; split it if it's two things.")


def handle_update_current_context(
    user_id: UUID,
    input_dict: dict,
    now: datetime,
    *,
    context_service: CurrentContextService,
) -> str:
    action = str(input_dict.get("action") or "add").strip()
    text = str(input_dict.get("text") or "").strip()
    if not text:
        return refused("text is required.")
    today = now.date()
    try:
        if action == "remove":
            gone = context_service.remove(user_id, text, today=today)
            if gone is None:
                live = context_service.live(user_id, today)
                return refused("No single line matches that. Live lines:\n" + "\n".join(
                    f"  {e.text}" for e in live)) if live else failed("Nothing live to remove.")
            return done(f"Removed: {gone.text}")
        until = None
        raw_until = str(input_dict.get("until") or "").strip()
        if raw_until:
            try:
                until = date.fromisoformat(raw_until)
            except ValueError:
                return refused("until must be YYYY-MM-DD.")
        entry, similar = context_service.add(user_id, text, today=today, until=until)
        out = f"Saved: {entry.text}"
        if similar:
            out += f"\nClose to a line already there: \"{similar}\" — remove that one if this replaces it."
        return done(out)
    except TooLong as exc:
        return _too_long(exc)
    except AtCap as exc:
        return failed("Not saved — the context is full. Remove one first:\n"
                      + "\n".join(f"  {e.text}" for e in exc.entries))
    except Exception:
        _log.exception("update_current_context failed for user %s", user_id)
        return unknown("That hit an error part-way — it may or may not have saved. Read what is stored before saying which.")


# --- save_preferences -------------------------------------------------------

SAVE_PREFERENCES_TOOL = {
    "name": "save_preferences",
    "description": (
        "Their standing rules for you, one per row with an id. add: a new rule "
        "(global = every turn; a house domain = with that house). list: every "
        "rule with its id — read before update or remove. update/remove: one "
        "rule by rule_id. Over 10 words is refused. They review the rows in the vault."
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
                "description": "add: where it applies. global when in doubt.",
            },
            "text": {"type": "string", "description": "add/update: the rule, second person, 10 words max. Two things = two rows."},
            "rule_id": {"type": "string", "description": "update/remove: from list."},
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
    guard: LineGuard | None = None,
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
                return done("No preference rules saved yet.")
            lines = ["Preference rules:"]
            for r in rules:
                lines.append(f"  [{r['id']}] ({r['domain']}) {r['rule']}")
            return done("\n".join(lines))

        if action == "add":
            domain = str(input_dict.get("domain", "")).strip() or "global"
            if not text:
                return refused("text is required to add a rule.")
            similar = None
            if guard is not None:
                guard.check_length(text)
                similar = guard.similar(
                    text, [r["rule"] for r in preferences_repository.list_rules(user_id)])
            preferences_repository.add_rule(user_id, domain, text)
            _refresh()
            out = f"Rule saved ({domain})."
            if similar:
                out += f"\nClose to one already held: \"{similar}\" — one home per rule; update or remove if this replaces it."
            return done(out)

        if action == "update":
            rid = str(input_dict.get("rule_id", "")).strip()
            if not rid or not text:
                return refused("rule_id and text are required to update.")
            if guard is not None:
                guard.check_length(text)
            if not preferences_repository.update_rule(user_id, UUID(rid), text):
                return refused("No rule with that id — action='list' shows them.")
            _refresh()
            return done("Rule updated.")

        if action == "remove":
            rid = str(input_dict.get("rule_id", "")).strip()
            if not rid:
                return refused("rule_id is required to remove.")
            if not preferences_repository.remove_rule(user_id, UUID(rid)):
                return refused("No rule with that id — action='list' shows them.")
            _refresh()
            return done("Rule removed.")

        return refused("Unknown action. Use: add, list, update, remove.")
    except TooLong as exc:
        return _too_long(exc)
    except ValueError:
        return refused("That rule_id isn't a valid id.")
    except Exception:
        _log.exception("save_preferences failed for user %s", user_id)
        return unknown("That hit an error part-way — it may or may not have saved. Read what is stored before saying which.")

def meta_tools(
    context_service: CurrentContextService,
    preferences_repository,
    brain_changed=None,   # (user_id) -> None: refresh the vault's Brain pages
    guard: LineGuard | None = None,
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
            brain_changed=lambda: _refresh(uid), guard=guard,
        )

    return [
        (UPDATE_CONTEXT_TOOL, _context),
        (SAVE_PREFERENCES_TOOL, _prefs),
    ]
