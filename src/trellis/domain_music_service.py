"""Music domain service — Spotify connection + live access tokens.

Thin: validates, calls the OAuth client + repo, returns typed data. It never
formats strings — that belongs to the tool handler (added with #7/#8). The
repository/OAuth Protocols it depends on are declared here (as with the second
brain domain).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from trellis.domain_music_models import SpotifyCredentials
from trellis.infra_spotify import SpotifyToken

_REFRESH_LEEWAY = timedelta(seconds=60)


class SpotifyOAuth(Protocol):
    def authorize_url(self, state: str) -> str | None: ...
    def exchange_code(self, code: str) -> SpotifyToken | None: ...
    def refresh(self, refresh_token: str) -> SpotifyToken | None: ...


class MusicRepository(Protocol):
    def save_credentials(self, credentials: SpotifyCredentials) -> None: ...
    def get_credentials(self, user_id: UUID) -> SpotifyCredentials | None: ...


class MusicService:
    def __init__(self, repo: MusicRepository, spotify: SpotifyOAuth) -> None:
        self._repo = repo
        self._spotify = spotify

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
