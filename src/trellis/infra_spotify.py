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
_API_URL = "https://api.spotify.com/v1"
_TIMEOUT = 20.0
_PAGE = 50            # Spotify's max page size for the list endpoints

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


@dataclass(frozen=True)
class SpotifyTrackData:
    """A track as returned by the Web API (raw metadata; the domain maps this to
    domain_music_models.Track). Artists are (id, name) pairs so genres can be
    looked up by artist id."""
    spotify_id: str
    name: str
    artists: tuple[tuple[str, str], ...]
    album_name: str | None
    popularity: int | None
    external_url: str | None
    preview_url: str | None


@dataclass(frozen=True)
class CreatedPlaylist:
    """A playlist just created on Spotify — its id (to add tracks) and the public
    URL to hand the user."""
    playlist_id: str
    name: str
    external_url: str | None


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

    # -- Web API data (current api.spotify.com/v1 endpoints; Feb-2026 migration
    #    renamed playlist tracks -> items, which is reflected below) --------------

    def _api_get(self, access_token: str, path: str, params: dict | None = None) -> dict | None:
        try:
            response = httpx.get(
                f"{_API_URL}{path}",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            _log.warning("Spotify GET %s failed", path, exc_info=True)
            return None

    @staticmethod
    def _parse_track(raw: dict | None) -> "SpotifyTrackData | None":
        if not raw or not raw.get("id") or not raw.get("name"):
            return None
        artists = tuple(
            (a.get("id", ""), a.get("name", ""))
            for a in (raw.get("artists") or [])
            if a.get("name")
        )
        album = raw.get("album") or {}
        return SpotifyTrackData(
            spotify_id=raw["id"],
            name=raw["name"],
            artists=artists,
            album_name=album.get("name"),
            popularity=raw.get("popularity"),
            external_url=(raw.get("external_urls") or {}).get("spotify"),
            preview_url=raw.get("preview_url"),
        )

    def _paged_tracks(
        self, access_token: str, path: str, max_items: int, *, track_key: str | None
    ) -> list[SpotifyTrackData] | None:
        """Page through a list endpoint. track_key names the wrapper field holding
        the track ('track' for saved/playlist... , 'item' for the Feb-2026 playlist
        /items shape); None means each item IS the track (top tracks)."""
        out: list[SpotifyTrackData] = []
        offset = 0
        while len(out) < max_items:
            data = self._api_get(access_token, path, {"limit": _PAGE, "offset": offset})
            if data is None:
                return out or None
            items = data.get("items") or []
            for item in items:
                raw = item.get(track_key) if track_key else item
                track = self._parse_track(raw)
                if track is not None:
                    out.append(track)
            if len(items) < _PAGE:
                break
            offset += _PAGE
        return out[:max_items]

    def get_saved_tracks(self, access_token: str, max_items: int = 300) -> list[SpotifyTrackData] | None:
        return self._paged_tracks(access_token, "/me/tracks", max_items, track_key="track")

    def get_top_tracks(
        self, access_token: str, time_range: str, max_items: int = 100
    ) -> list[SpotifyTrackData] | None:
        return self._paged_tracks(
            access_token, f"/me/top/tracks?time_range={time_range}", max_items, track_key=None
        )

    def get_recently_played(self, access_token: str, limit: int = 50) -> list[SpotifyTrackData] | None:
        data = self._api_get(access_token, "/me/player/recently-played", {"limit": min(limit, 50)})
        if data is None:
            return None
        out: list[SpotifyTrackData] = []
        for item in data.get("items") or []:
            track = self._parse_track(item.get("track"))
            if track is not None:
                out.append(track)
        return out

    def get_user_playlists(self, access_token: str, limit: int = 20) -> list[tuple[str, str]] | None:
        data = self._api_get(access_token, "/me/playlists", {"limit": min(limit, 50)})
        if data is None:
            return None
        return [
            (p["id"], p.get("name", ""))
            for p in (data.get("items") or [])
            if p and p.get("id")
        ]

    def get_playlist_items(
        self, access_token: str, playlist_id: str, max_items: int = 50
    ) -> list[SpotifyTrackData] | None:
        # /playlists/{id}/items (the /tracks endpoint was retired Feb 2026); the
        # nested track sits under 'item', not 'track'.
        return self._paged_tracks(
            access_token,
            f"/playlists/{playlist_id}/items"
            "?fields=items(item(id,name,artists,album(name),popularity,preview_url,external_urls)),next",
            max_items,
            track_key="item",
        )

    def get_artists_genres(self, access_token: str, artist_ids: list[str]) -> dict[str, list[str]]:
        """Map artist id -> genres, batching by 50. Best-effort: on failure (e.g. a
        dev-mode quota 403) an artist just gets no genres, and the sync proceeds."""
        genres: dict[str, list[str]] = {}
        unique = [a for a in dict.fromkeys(artist_ids) if a]
        for start in range(0, len(unique), 50):
            batch = unique[start:start + 50]
            data = self._api_get(access_token, "/artists", {"ids": ",".join(batch)})
            if data is None:
                continue
            for artist in data.get("artists") or []:
                if artist and artist.get("id"):
                    genres[artist["id"]] = list(artist.get("genres") or [])
        return genres

    # -- Web API writes (playlists) -------------------------------------------

    def _api_post(self, access_token: str, path: str, body: dict) -> dict | None:
        try:
            response = httpx.post(
                f"{_API_URL}{path}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            return response.json() if response.content else {}
        except Exception:
            _log.warning("Spotify POST %s failed", path, exc_info=True)
            return None

    def create_playlist(
        self, access_token: str, name: str, description: str = ""
    ) -> CreatedPlaylist | None:
        # POST /me/playlists — the old /users/{id}/playlists was retired in the
        # Feb 2026 migration and /me/playlists needs no caller user id. Private by
        # default; playlist-modify-private scope is requested up front.
        data = self._api_post(
            access_token, "/me/playlists",
            {"name": name, "description": description, "public": False},
        )
        if not data or not data.get("id"):
            return None
        return CreatedPlaylist(
            playlist_id=data["id"],
            name=data.get("name", name),
            external_url=(data.get("external_urls") or {}).get("spotify"),
        )

    def add_tracks_to_playlist(
        self, access_token: str, playlist_id: str, spotify_track_ids: list[str]
    ) -> bool:
        # POST /playlists/{id}/items (the /tracks endpoint was retired Feb 2026;
        # the body shape is unchanged). Batches of 100 per Spotify's limit.
        if not spotify_track_ids:
            return True
        for start in range(0, len(spotify_track_ids), 100):
            batch = spotify_track_ids[start:start + 100]
            uris = [f"spotify:track:{tid}" for tid in batch]
            if self._api_post(access_token, f"/playlists/{playlist_id}/items", {"uris": uris}) is None:
                return False
        return True
