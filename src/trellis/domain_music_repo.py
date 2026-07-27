"""Music domain storage — Spotify credentials (Postgres).

The repository Protocol lives in domain_music_service.py (as with the second
brain domain); this file is the concrete Postgres implementation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg2.extras import Json, RealDictCursor

from trellis.domain_music_models import SpotifyCredentials, Track


class PostgresMusicRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    # -- tracks ---------------------------------------------------------------

    def upsert_tracks(self, user_id: UUID, tracks: list[Track], *, now: datetime) -> int:
        """Insert/update the user's tracks (keyed by user + spotify id). Genres are
        only overwritten when the new set is non-empty, so a later sync that missed
        the artist/genre call doesn't wipe genres we already have."""
        if not tracks:
            return 0
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                for t in tracks:
                    cur.execute(
                        """
                        INSERT INTO spotify_tracks
                            (user_id, spotify_id, name, artists, album_name, genres,
                             popularity, external_url, preview_url, synced_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (user_id, spotify_id) DO UPDATE SET
                            name = EXCLUDED.name,
                            artists = EXCLUDED.artists,
                            album_name = EXCLUDED.album_name,
                            genres = CASE WHEN array_length(EXCLUDED.genres, 1) > 0
                                          THEN EXCLUDED.genres ELSE spotify_tracks.genres END,
                            popularity = EXCLUDED.popularity,
                            external_url = EXCLUDED.external_url,
                            preview_url = EXCLUDED.preview_url,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (
                            user_id, t.spotify_id, t.name,
                            Json([{"id": a.id, "name": a.name} for a in t.artists]),
                            t.album_name, list(t.genres), t.popularity,
                            t.external_url, t.preview_url, now, now,
                        ),
                    )
        return len(tracks)

    def tracks_missing_embedding(
        self, user_id: UUID
    ) -> list[tuple[UUID, str, list[str], list[str]]]:
        """(track uuid, name, artist names, genres) for tracks not yet filed into
        the meaning index — so the sync's embed step is re-runnable and only spends
        embedding requests on new tracks."""
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT t.id, t.name, t.artists, t.genres
                    FROM spotify_tracks t
                    LEFT JOIN memory_index m
                        ON m.entity_kind = 'track' AND m.entity_id = t.id
                    WHERE t.user_id = %s AND m.id IS NULL
                    """,
                    (user_id,),
                )
                return [
                    (
                        row["id"],
                        row["name"],
                        [a.get("name", "") for a in (row["artists"] or [])],
                        list(row["genres"] or []),
                    )
                    for row in cur.fetchall()
                ]

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
