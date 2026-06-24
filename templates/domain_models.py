"""
TEMPLATE: domain_models.py
Copy to: src/trellis/{domain}_models.py

Rules:
- Frozen dataclasses only
- No I/O of any kind
- No imports from other trellis modules
- Everything the domain needs to represent its data lives here
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID


@dataclass(frozen=True)
class ExampleRecord:
    id: UUID
    user_id: UUID
    value: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def summary(self) -> str:
        return self.value
