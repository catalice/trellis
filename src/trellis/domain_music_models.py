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


@dataclass(frozen=True, slots=True)
class ArtistRef:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Track:
    """A track from the user's Spotify library (metadata only — the embedding
    lives in the shared memory_index, keyed by this track's stored UUID)."""
    spotify_id: str
    name: str
    artists: tuple[ArtistRef, ...]
    album_name: str | None
    genres: tuple[str, ...]
    popularity: int | None
    external_url: str | None
    preview_url: str | None

    @staticmethod
    def compose_embedding_text(
        name: str, artist_names: list[str], genres: list[str]
    ) -> str:
        """The text a track is embedded from — name, artists, genres. Single source
        of truth (like Capture/Effort.compose_embedding_text), shared by the sync's
        embed step so it can never drift. Spotify's own audio-features/recommendation
        endpoints are gone, so meaning is built from this text, not acoustics."""
        who = ", ".join(n for n in artist_names if n) or "unknown artist"
        genre_text = ", ".join(g for g in genres if g) or "unknown"
        return f"{name} by {who} — genres: {genre_text}"

    def embedding_text(self) -> str:
        return self.compose_embedding_text(
            self.name, [a.name for a in self.artists], list(self.genres)
        )


@dataclass(frozen=True, slots=True)
class StoredTrack:
    """A track already synced to the library, carrying its internal UUID (the
    meaning-index key) and spotify_id (needed to add it to a playlist). Returned
    by recommendations so the companion can talk about tracks and act on them."""
    id: UUID
    spotify_id: str
    name: str
    artist_names: tuple[str, ...]
    genres: tuple[str, ...]

    def describe(self) -> str:
        """A one-line label — 'name — artist1, artist2'. Presentation-neutral;
        the tool handler decides final formatting."""
        who = ", ".join(n for n in self.artist_names if n)
        return f"{self.name} — {who}" if who else self.name
