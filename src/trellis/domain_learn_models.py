"""Learn house models — deliberate understanding, built bottom-up.
Frozen dataclasses only; no I/O, no imports from other trellis modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID


class EntryKind(StrEnum):
    MATERIAL = "material"   # something learned/placed — their words or a digest
    SOURCE = "source"       # a kept reference — MUST carry source_url (fetched, not recalled)
    TEST = "test"           # a retrieval-practice outcome — question, their answer, verdict


@dataclass(frozen=True)
class LearnThread:
    """A topic being built bottom-up. The map is drawn by the user — regions
    are their labels; position is 'you are here' in plain words."""
    id: UUID
    user_id: UUID
    title: str
    position: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class LearnEntry:
    id: UUID
    user_id: UUID
    thread_id: UUID
    kind: EntryKind
    content: str
    region: str | None = None           # where THEY placed it on the map
    source_url: str | None = None
    source_title: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
