from __future__ import annotations

from typing import Protocol
from uuid import UUID

DOMAINS = ("training", "learn", "ef", "notes")


class PreferencesRepository(Protocol):
    def get(self, user_id: UUID, domain: str) -> str | None: ...
    def set(self, user_id: UUID, domain: str, content: str) -> None: ...


class InMemoryPreferencesRepository:
    def __init__(self) -> None:
        self._store: dict[tuple, str] = {}

    def get(self, user_id: UUID, domain: str) -> str | None:
        return self._store.get((user_id, domain))

    def set(self, user_id: UUID, domain: str, content: str) -> None:
        self._store[(user_id, domain)] = content
