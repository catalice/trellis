from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from trellis.registry import ContextLoader


class _LearningService(Protocol):
    def thread_state(self, user_id: UUID) -> list[dict]: ...


class _PreferencesRepo(Protocol):
    def get(self, user_id: UUID, domain: str) -> str | None: ...


def learn_context_loader(
    learning_service: _LearningService,
    preferences_repository: _PreferencesRepo,
) -> ContextLoader:
    def loader(user_id: UUID, now: datetime) -> str | None:
        state = learning_service.thread_state(user_id)
        if not state:
            return None

        lines = ["[Learning]\nActive learning threads:"]
        for item in state:
            thread = item["thread"]
            entries = item["recent_entries"]
            lines.append(f"  Thread: {thread.name} (id: {thread.id})")
            if thread.description:
                lines.append(f"    Scope: {thread.description}")
            if entries:
                covered = "; ".join(e.summary for e in entries)
                lines.append(f"    Covered so far: {covered}")
            else:
                lines.append(f"    No entries yet — start from the very beginning.")

        prefs = preferences_repository.get(user_id, "learn")
        if prefs:
            lines.append(f"\n[Your learning preferences]\n{prefs}")

        return "\n".join(lines)

    return loader
