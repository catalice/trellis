"""
Spotify — OAuth + token refresh for the music domain.

External-service client, like infra_garmin / infra_search. Owns only the OAuth
dance against accounts.spotify.com: build the authorize URL, exchange an auth
code for tokens, refresh an expired access token. The Web API *data* calls
(library sync, playlists) live with the music domain and use the access token
this produces.

Graceful by design: a failed exchange/refresh logs and returns None rather than
raising. Read AND write scopes are requested up front, so adding playlist
creation later never forces the user to reconnect.

Endpoints are Spotify's standard OAuth endpoints (accounts.spotify.com), which
the Feb 2026 Web API migration did not touch.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

import httpx

_log = logging.getLogger(__name__)

_AUTH_URL = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_TIMEOUT = 20.0

# Requested up front — read for sync, write so playlist creation needs no reconnect.
SCOPES = " ".join((
    "user-library-read",
    "user-top-read",
    "user-read-recently-played",
    "playlist-read-private",
    "playlist-modify-public",
    "playlist-modify-private",
))


@dataclass(frozen=True)
class SpotifyToken:
    access_token: str
    refresh_token: str
    scope: str
    expires_in: int          # seconds from issue until the access token expires


class SpotifyOAuth(Protocol):
    def authorize_url(self, state: str) -> str | None: ...
    def exchange_code(self, code: str) -> "SpotifyToken | None": ...
    def refresh(self, refresh_token: str) -> "SpotifyToken | None": ...


class SpotifyClient:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    @property
    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret and self._redirect_uri)

    def authorize_url(self, state: str) -> str | None:
        if not self.is_configured:
            return None
        params = urlencode({
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "scope": SCOPES,
            "state": state,
        })
        return f"{_AUTH_URL}?{params}"

    def exchange_code(self, code: str) -> SpotifyToken | None:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
            },
            fallback_refresh="",
        )

    def refresh(self, refresh_token: str) -> SpotifyToken | None:
        return self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
            fallback_refresh=refresh_token,
        )

    def _basic_auth(self) -> str:
        raw = f"{self._client_id}:{self._client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _token_request(self, data: dict, *, fallback_refresh: str) -> SpotifyToken | None:
        if not self.is_configured:
            return None
        try:
            response = httpx.post(
                _TOKEN_URL,
                headers={
                    "Authorization": self._basic_auth(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=data,
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            body = response.json()
        except Exception:
            _log.warning("Spotify token request failed (%s)", data.get("grant_type"), exc_info=True)
            return None

        access = body.get("access_token")
        if not access:
            _log.warning("Spotify token response missing access_token")
            return None
        # Spotify omits refresh_token on a refresh — keep the one we already hold.
        return SpotifyToken(
            access_token=access,
            refresh_token=body.get("refresh_token") or fallback_refresh,
            scope=body.get("scope", ""),
            expires_in=int(body.get("expires_in", 3600)),
        )
