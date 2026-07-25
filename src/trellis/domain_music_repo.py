"""Music domain storage — Spotify credentials (Postgres).

The repository Protocol lives in domain_music_service.py (as with the second
brain domain); this file is the concrete Postgres implementation.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg2.extras import RealDictCursor

from trellis.domain_music_models import SpotifyCredentials


class PostgresMusicRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    def save_credentials(self, c: SpotifyCredentials) -> None:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO spotify_credentials
                        (user_id, access_token, refresh_token, scope,
                         expires_at, connected_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        access_token = EXCLUDED.access_token,
                        refresh_token = EXCLUDED.refresh_token,
                        scope = EXCLUDED.scope,
                        expires_at = EXCLUDED.expires_at,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        c.user_id, c.access_token, c.refresh_token, c.scope,
                        c.expires_at, c.connected_at, c.updated_at,
                    ),
                )

    def get_credentials(self, user_id: UUID) -> SpotifyCredentials | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM spotify_credentials WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
                return _credentials(row) if row else None


def _credentials(row: dict) -> SpotifyCredentials:
    return SpotifyCredentials(
        user_id=row["user_id"],
        access_token=row["access_token"],
        refresh_token=row["refresh_token"],
        scope=row["scope"],
        expires_at=row["expires_at"],
        connected_at=row["connected_at"],
        updated_at=row["updated_at"],
    )
