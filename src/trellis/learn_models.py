from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class LearningThread:
    id: UUID
    user_id: UUID
    name: str
    description: str | None
    is_active: bool
    created_at: datetime


@dataclass(frozen=True)
class LearningEntry:
    id: UUID
    user_id: UUID
    thread_id: UUID
    summary: str
    created_at: datetime
