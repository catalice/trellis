"""Music domain models — frozen dataclasses only. No I/O, no trellis imports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SpotifyCredentials:
    user_id: UUID
    access_token: str
    refresh_token: str
    scope: str
    expires_at: datetime
    connected_at: datetime
    updated_at: datetime

    def is_expired(self, now: datetime, *, leeway_seconds: int = 60) -> bool:
        """True if the access token is expired (or within `leeway` of it), so the
        caller should refresh before using it."""
        return now >= self.expires_at - timedelta(seconds=leeway_seconds)
