"""Music domain service — Spotify connection + live access tokens.

Thin: validates, calls the OAuth client + repo, returns typed data. It never
formats strings — that belongs to the tool handler (added with #7/#8). The
repository/OAuth Protocols it depends on are declared here (as with the second
brain domain).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from trellis.domain_music_models import ArtistRef, SpotifyCredentials, Track
from trellis.infra_spotify import SpotifyToken, SpotifyTrackData

_REFRESH_LEEWAY = timedelta(seconds=60)
_TOP_WINDOWS = ("short_term", "medium_term", "long_term")


class SpotifyApi(Protocol):
    def authorize_url(self, state: str) -> str | None: ...
    def exchange_code(self, code: str) -> SpotifyToken | None: ...
    def refresh(self, refresh_token: str) -> SpotifyToken | None: ...
    def get_saved_tracks(self, access_token: str, max_items: int = 300) -> list[SpotifyTrackData] | None: ...
    def get_top_tracks(self, access_token: str, time_range: str, max_items: int = 100) -> list[SpotifyTrackData] | None: ...
    def get_recently_played(self, access_token: str, limit: int = 50) -> list[SpotifyTrackData] | None: ...
    def get_user_playlists(self, access_token: str, limit: int = 20) -> list[tuple[str, str]] | None: ...
    def get_playlist_items(self, access_token: str, playlist_id: str, max_items: int = 50) -> list[SpotifyTrackData] | None: ...
    def get_artists_genres(self, access_token: str, artist_ids: list[str]) -> dict[str, list[str]]: ...


class MusicRepository(Protocol):
    def save_credentials(self, credentials: SpotifyCredentials) -> None: ...
    def get_credentials(self, user_id: UUID) -> SpotifyCredentials | None: ...
    def upsert_tracks(self, user_id: UUID, tracks: list[Track], *, now: datetime) -> int: ...
    def tracks_missing_embedding(self, user_id: UUID) -> list[tuple[UUID, str, list[str], list[str]]]: ...


class MeaningIndex(Protocol):
    def remember_many(self, items: list[tuple[UUID, str, UUID, str]]) -> int: ...


@dataclass(frozen=True)
class SyncSummary:
    connected: bool
    tracks_synced: int
    tracks_embedded: int


class MusicService:
    def __init__(self, repo: MusicRepository, spotify: SpotifyApi, memory: MeaningIndex) -> None:
        self._repo = repo
        self._spotify = spotify
        self._memory = memory

    def connect_url(self, state: str) -> str | None:
        """The Spotify authorize URL to hand the user, or None if unconfigured."""
        return self._spotify.authorize_url(state)

    def complete_connection(self, user_id: UUID, code: str, *, now: datetime) -> bool:
        """Exchange an auth code for tokens and store them. True on success."""
        token = self._spotify.exchange_code(code)
        if token is None:
            return False
        self._repo.save_credentials(_to_credentials(user_id, token, now, connected_at=now))
        return True

    def is_connected(self, user_id: UUID) -> bool:
        return self._repo.get_credentials(user_id) is not None

    def valid_access_token(self, user_id: UUID, *, now: datetime) -> str | None:
        """A live access token — refreshing and re-storing when the stored one has
        expired. None if not connected, or if the refresh fails."""
        creds = self._repo.get_credentials(user_id)
        if creds is None:
            return None
        if now < creds.expires_at - _REFRESH_LEEWAY:
            return creds.access_token
        token = self._spotify.refresh(creds.refresh_token)
        if token is None:
            return None
        refreshed = _to_credentials(user_id, token, now, connected_at=creds.connected_at)
        self._repo.save_credentials(refreshed)
        return refreshed.access_token

    def sync_library(self, user_id: UUID, *, now: datetime) -> SyncSummary:
        """Pull the user's library from Spotify, store the tracks, and file any new
        ones into the shared meaning index (so recall + the companion span them).
        Re-runnable: a source that fails is skipped (not fatal), and a rate-limit
        mid-embed just leaves the rest for the next run (only un-indexed tracks are
        embedded)."""
        token = self.valid_access_token(user_id, now=now)
        if token is None:
            return SyncSummary(connected=False, tracks_synced=0, tracks_embedded=0)

        raw: dict[str, SpotifyTrackData] = {}

        def collect(tracks: list[SpotifyTrackData] | None) -> None:
            for track in tracks or []:
                raw.setdefault(track.spotify_id, track)

        collect(self._spotify.get_saved_tracks(token))
        for window in _TOP_WINDOWS:
            collect(self._spotify.get_top_tracks(token, window))
        collect(self._spotify.get_recently_played(token))
        for playlist_id, _name in (self._spotify.get_user_playlists(token) or []):
            collect(self._spotify.get_playlist_items(token, playlist_id))

        # Genres come from the artists endpoint — best-effort (may 403 on dev quota).
        artist_ids = [artist_id for track in raw.values() for artist_id, _ in track.artists]
        genres_by_artist = self._spotify.get_artists_genres(token, artist_ids)

        tracks = [_to_track(t, genres_by_artist) for t in raw.values()]
        synced = self._repo.upsert_tracks(user_id, tracks, now=now)

        missing = self._repo.tracks_missing_embedding(user_id)
        items = [
            (user_id, "track", track_id, Track.compose_embedding_text(name, artist_names, genres))
            for (track_id, name, artist_names, genres) in missing
        ]
        embedded = self._memory.remember_many(items) if items else 0
        return SyncSummary(connected=True, tracks_synced=synced, tracks_embedded=embedded)


def _to_credentials(
    user_id: UUID, token: SpotifyToken, now: datetime, *, connected_at: datetime
) -> SpotifyCredentials:
    return SpotifyCredentials(
        user_id=user_id,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        scope=token.scope,
        expires_at=now + timedelta(seconds=token.expires_in),
        connected_at=connected_at,
        updated_at=now,
    )


def _to_track(data: SpotifyTrackData, genres_by_artist: dict[str, list[str]]) -> Track:
    genres = sorted({
        genre
        for artist_id, _ in data.artists
        for genre in genres_by_artist.get(artist_id, [])
    })
    return Track(
        spotify_id=data.spotify_id,
        name=data.name,
        artists=tuple(ArtistRef(id=aid, name=aname) for aid, aname in data.artists),
        album_name=data.album_name,
        genres=tuple(genres),
        popularity=data.popularity,
        external_url=data.external_url,
        preview_url=data.preview_url,
    )
