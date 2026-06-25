"""
Context loader for the notes domain.

Surfaces recent captures (with synthesis) so the oracle knows what's been
captured but not yet actioned.

Usage in main.py:
    registry.add_domain("notes", notes_context_loader(...), ...)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol
from uuid import UUID

from trellis.registry import ContextLoader

_log = logging.getLogger(__name__)


# --- Protocol (structural — no imports from service files) ------------------

class _CaptureRepo(Protocol):
    def list_recent(self, user_id: UUID, limit: int) -> list: ...


class _PreferencesRepo(Protocol):
    def get(self, user_id: UUID, domain: str) -> str | None: ...


# --- Factory ----------------------------------------------------------------

def notes_context_loader(
    capture_repository: _CaptureRepo,
    preferences_repository: _PreferencesRepo,
) -> ContextLoader:
    def loader(user_id: UUID, now: datetime) -> str | None:
        parts: list[str] = []
        try:
            captures = capture_repository.list_recent(user_id, limit=10)
            if captures:
                lines = [f"Recent captures ({len(captures)}):"]
                for c in captures:
                    local = c.created_at.astimezone(now.tzinfo) if now.tzinfo else c.created_at
                    lines.append(f"  [{local.strftime('%d %b %H:%M')}] {c.synthesis}")
                parts.append("\n".join(lines))
        except Exception:
            _log.warning("notes_context: captures load failed", exc_info=True)

        prefs = preferences_repository.get(user_id, "notes")
        if prefs:
            parts.append(f"[Your notes preferences]\n{prefs}")

        if not parts:
            return None
        return "[Notes]\n" + "\n\n".join(parts)

    return loader
