from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from trellis.learn_models import LearningEntry, LearningThread
from trellis.learn_repo import LearningEntryRepository, LearningThreadRepository

_RECENT_ENTRIES_PER_THREAD = 5


class LearningService:
    def __init__(
        self,
        thread_repository: LearningThreadRepository,
        entry_repository: LearningEntryRepository,
    ) -> None:
        self._threads = thread_repository
        self._entries = entry_repository

    def create_thread(
        self,
        user_id: UUID,
        name: str,
        description: str | None = None,
    ) -> LearningThread:
        thread = LearningThread(
            id=uuid4(),
            user_id=user_id,
            name=name.strip(),
            description=description.strip() if description else None,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        return self._threads.save(thread)

    def list_threads(self, user_id: UUID) -> list[LearningThread]:
        return self._threads.list_active(user_id)

    def record_entry(
        self,
        user_id: UUID,
        thread_id: UUID,
        summary: str,
    ) -> LearningEntry:
        entry = LearningEntry(
            id=uuid4(),
            user_id=user_id,
            thread_id=thread_id,
            summary=summary.strip(),
            created_at=datetime.now(timezone.utc),
        )
        return self._entries.save(entry)

    def thread_state(self, user_id: UUID) -> list[dict]:
        threads = self._threads.list_active(user_id)
        result = []
        for thread in threads:
            entries = self._entries.recent(thread.id, limit=_RECENT_ENTRIES_PER_THREAD)
            result.append({"thread": thread, "recent_entries": entries})
        return result

    def get_thread_history(self, user_id: UUID, name_reference: str, limit: int = 50) -> dict | None:
        threads = self._threads.list_active(user_id)
        query = name_reference.lower()
        exact = [t for t in threads if t.name.lower() == query]
        partial = [t for t in threads if query in t.name.lower()]
        candidates = exact or partial
        if not candidates:
            return None
        thread = candidates[0]
        entries = self._entries.recent(thread.id, limit=limit)
        return {"thread": thread, "entries": entries}
