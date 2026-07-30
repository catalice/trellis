"""
Tool schemas and handlers for the music domain — the creative companion.

The domain is a conversation, not a command menu: the oracle (wearing the
companion voice, injected by the context loader below) talks music with Cat and
reaches for these tools when an ACTION is needed. Tools execute; they never judge
— which tracks fit a vibe is the oracle's call (it curates the recall shortlist
inline), and arranging a deliberate set's arc is domain_music_claude's call.

Handler signature: (user_id, input_dict, now) -> str
Context loader: music_context_loader
Registration: music_tools(...)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable
from uuid import UUID

_log = logging.getLogger(__name__)

# (user_id, now) -> context string or None
ContextLoader = Callable[[UUID, datetime], "str | None"]


# ---------------------------------------------------------------------------
# Companion voice — DRAFT FOR REVIEW (the human wants to shape this).
#
# Injected as domain-routed context when music is active (see music_context_loader),
# NOT baked into the shared system prompt — so it colours the voice only when
# talking music, and stays easy to tune here. It guides the oracle; it is not a
# second Claude call.
# ---------------------------------------------------------------------------

MUSIC_COMPANION_PERSONA = """\
[Music companion]
Right now you're Cat's music companion — a sounding board for her creative \
practice (DJing on the Traktor S4, the PO-33, whatever she's making), not a \
librarian and not a genre snob. Her taste is gloriously eclectic: nostalgic \
anthems, techno, Bicep, drum & bass, oddball gems. That range is a strength, not \
a problem to tidy up.

How to be:
- A buddy to vibe off. She thinks out loud to get unstuck — meet the talk, bounce \
ideas back, help her start. Getting her to the decks matters more than being right.
- Mix by feeling, not rules. Bridge tracks on mood, energy and nostalgia — across \
genres, across tempo. The weird segue is the good one. Never lecture about BPM or \
"correct" key matching unless she asks the technical question.
- Talk about HER actual library. Use recommend_tracks to pull real tracks from her \
own collection for a vibe, then curate them yourself — pick the ones that fit, put \
them in an order that flows, say why. Don't recite the whole list back.
- When she wants a real playlist, build it: pick the tracks in conversation, then \
call create_playlist with those track ids (or hand a vibe to build a set with an arc).
- Think in energy arcs and the shape of a set — where it opens, lifts, lands — \
because driving the energy is the point of DJing for her.
- Warm, a bit playful, honest. Celebrate that she's touching the gear at all.\
"""


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

MUSIC_GET_TOOL: dict = {
    "name": "music_get",
    "description": (
        "Read music-domain status. what='connection': is Spotify connected, and if "
        "not, the link to hand Cat to connect. what='library': how many of her tracks "
        "are synced and searchable. Call connection before recommend/playlist actions "
        "if you're unsure she's set up."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {"type": "string", "enum": ["connection", "library"]},
        },
        "required": ["what"],
    },
}

RECOMMEND_TRACKS_TOOL: dict = {
    "name": "recommend_tracks",
    "description": (
        "Pull a shortlist of Cat's OWN tracks that match a vibe, by meaning (mood, "
        "feel, era) — not genre or tempo. Use this when she wants something to play, "
        "practise with, or build a set from ('what goes with a euphoric nostalgia "
        "vibe', 'give me something dark and rolling'). It returns candidates with "
        "their ids; YOU then curate — pick the ones that fit, order them, say why. "
        "Don't just recite the list."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "vibe": {
                "type": "string",
                "description": "The vibe/feeling/mood/era to match. A phrase works better than one word.",
            },
            "limit": {
                "type": "integer",
                "description": "How many candidates to pull (default 15).",
                "default": 15,
            },
        },
        "required": ["vibe"],
    },
}

CREATE_PLAYLIST_TOOL: dict = {
    "name": "create_playlist",
    "description": (
        "Create a real Spotify playlist in Cat's account. Two ways: pass track_ids "
        "(the ids from recommend_tracks, once you've picked and ordered them in "
        "conversation — preferred, keeps you in control of the selection), OR pass "
        "from_vibe to have a set recalled + arranged into an energy arc automatically. "
        "Prefer track_ids when you've already curated with her."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Playlist name. Omit only when using from_vibe (one is generated)."},
            "track_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ordered track UUIDs from recommend_tracks. Play order = this order.",
            },
            "from_vibe": {
                "type": "string",
                "description": "A vibe to auto-build a set from, when you haven't hand-picked tracks.",
            },
            "description": {"type": "string", "description": "Optional playlist description."},
        },
        "required": [],
    },
}

SYNC_LIBRARY_TOOL: dict = {
    "name": "sync_library",
    "description": (
        "Pull Cat's latest Spotify library (saved, top, recent, playlists) into "
        "Trellis and make it searchable by vibe. Run when she's just connected, or "
        "when she's added music and recommendations feel stale. Safe to re-run."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

MUSIC_CONNECT_TOOL: dict = {
    "name": "music_connect",
    "description": (
        "Complete the Spotify connection: after Cat approves the link from "
        "music_get(connection) and pastes back the redirect URL (or the code in it), "
        "pass that here to finish connecting. Extract the code if she pastes a full "
        "URL."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "The auth code from the redirect (or the whole redirect URL)."},
        },
        "required": ["code"],
    },
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_music_get(
    user_id: UUID, input_dict: dict, now: datetime, *, music_service
) -> str:
    what = str(input_dict.get("what", "")).strip()

    if what == "connection":
        if music_service.is_connected(user_id):
            return "Spotify is connected."
        url = music_service.connect_url("trellis-connect")
        if not url:
            return "Spotify isn't set up on this Trellis (no app credentials configured)."
        return (
            "Spotify isn't connected yet. Send Cat this link to approve, then have her "
            "paste back the URL it redirects to (or the code in it) so you can call "
            f"music_connect:\n{url}"
        )

    if what == "library":
        if not music_service.is_connected(user_id):
            return "Spotify isn't connected — nothing synced yet."
        count = music_service.library_size(user_id)
        if count == 0:
            return "Connected, but no tracks synced yet — run sync_library."
        return f"{count} tracks synced and searchable by vibe."

    return f"Unknown option: {what!r}. Use: connection, library."


def handle_recommend_tracks(
    user_id: UUID, input_dict: dict, now: datetime, *, music_service
) -> str:
    vibe = str(input_dict.get("vibe", "")).strip()
    if not vibe:
        return "vibe is required — the feeling/mood to match."
    try:
        limit = int(input_dict.get("limit", 15))
    except (TypeError, ValueError):
        limit = 15
    limit = max(1, min(limit, 30))

    candidates = music_service.recommend_for_vibe(user_id, vibe, now=now, limit=limit)
    if candidates is None:
        return "Vibe search is unavailable right now (embeddings aren't configured)."
    if not candidates:
        if not music_service.is_connected(user_id):
            return "No matches — Spotify isn't connected yet. Connect + sync her library first."
        return "No tracks in her library came close to that vibe. Try a different angle, or sync more music."

    lines = [f"Candidates for '{vibe}' (curate these — pick, order, say why; don't recite):"]
    for t in candidates:
        lines.append(f"  [{t.id}] {t.describe()}")
    return "\n".join(lines)


def handle_create_playlist(
    user_id: UUID, input_dict: dict, now: datetime, *, music_service
) -> str:
    name = str(input_dict.get("name", "")).strip()
    description = str(input_dict.get("description", "")).strip()
    raw_ids = input_dict.get("track_ids") or []
    from_vibe = str(input_dict.get("from_vibe", "")).strip()

    if raw_ids:
        if not name:
            return "name is required when creating a playlist from track_ids."
        track_ids: list[UUID] = []
        for value in raw_ids:
            try:
                track_ids.append(UUID(str(value)))
            except ValueError:
                continue
        if not track_ids:
            return "None of the track_ids were valid UUIDs — get them from recommend_tracks."
        built = music_service.create_playlist_from_tracks(
            user_id, name, track_ids, description=description, now=now
        )
    elif from_vibe:
        built = music_service.build_set_from_vibe(
            user_id, from_vibe, now=now, name=name or None
        )
    else:
        return "Provide either track_ids (preferred — the ones you curated) or from_vibe."

    if built is None:
        if not music_service.is_connected(user_id):
            return "Couldn't create it — Spotify isn't connected."
        return "Couldn't create the playlist (nothing matched, or Spotify rejected the write). Try again."

    lines = [f"Created '{built.name}' ({len(built.tracks)} tracks)."]
    if built.url:
        lines.append(built.url)
    if built.note:
        lines.append(built.note)
    lines.append("Tracks:")
    for t in built.tracks:
        lines.append(f"  • {t.describe()}")
    return "\n".join(lines)


def handle_sync_library(
    user_id: UUID, input_dict: dict, now: datetime, *, music_service
) -> str:
    summary = music_service.sync_library(user_id, now=now)
    if not summary.connected:
        return "Spotify isn't connected — connect first, then sync."
    embedded = (
        f", {summary.tracks_embedded} newly searchable"
        if summary.tracks_embedded
        else " (all already searchable)"
    )
    return f"Synced {summary.tracks_synced} tracks{embedded}."


def handle_music_connect(
    user_id: UUID, input_dict: dict, now: datetime, *, music_service
) -> str:
    raw = str(input_dict.get("code", "")).strip()
    if not raw:
        return "code is required — the auth code (or the redirect URL) from the Spotify approval."
    code = _extract_code(raw)
    if not code:
        return "Couldn't find an auth code in that. Paste the redirect URL or the code itself."
    if music_service.complete_connection(user_id, code, now=now):
        return "Spotify connected. Run sync_library to pull her music in."
    return "Connection failed — the code may have expired (they last ~a minute). Send the link again."


def _extract_code(raw: str) -> str:
    """Accept either a bare code or a full redirect URL with ?code=...&state=..."""
    if "code=" in raw:
        after = raw.split("code=", 1)[1]
        return after.split("&", 1)[0].strip()
    return raw


# ---------------------------------------------------------------------------
# Context loader — Tier 1b (loaded when music is the active domain)
# ---------------------------------------------------------------------------

def music_context_loader(music_service) -> ContextLoader:
    """Injects the companion voice + live connection/library state when music is
    routed. The persona is guidance for the oracle's voice this turn — not a second
    Claude call."""
    def loader(user_id: UUID, now: datetime) -> str | None:
        parts = [MUSIC_COMPANION_PERSONA]
        try:
            if music_service.is_connected(user_id):
                count = music_service.library_size(user_id)
                parts.append(
                    f"Spotify: connected, {count} tracks searchable by vibe."
                    if count
                    else "Spotify: connected, but no tracks synced yet — offer to sync_library."
                )
            else:
                parts.append("Spotify: not connected — offer to connect if she wants recommendations.")
        except Exception:
            _log.warning("music_context: status load failed", exc_info=True)
        return "\n\n".join(parts)

    return loader


# ---------------------------------------------------------------------------
# Routing signals
# ---------------------------------------------------------------------------

MUSIC_SIGNALS: list[str] = [
    "music", "song", "songs", "track", "tracks", "tune", "tunes",
    "playlist", "playlists", "spotify", "album",
    "dj", "djing", "mix", "mixing", "set ", "decks", "traktor", "bpm", "beatmatch",
    "vibe", "vibes", "listen", "play me", "what should i play",
    "techno", "house", "drum and bass", "dnb", "d&b", "bicep",
    "po-33", "po33", "pocket operator", "groovebox", "sampler",
]


# ---------------------------------------------------------------------------
# Registration factory
# ---------------------------------------------------------------------------

def music_tools(music_service) -> list[tuple[dict, Any]]:
    """All music tools. Returns [] when the domain isn't available (no Spotify
    credentials configured) so there are no dead tools — mirrors how web_search is
    gated. The caller passes music_service=None in that case."""
    if music_service is None:
        return []
    return [
        (
            MUSIC_GET_TOOL,
            lambda uid, inp, now: handle_music_get(uid, inp, now, music_service=music_service),
        ),
        (
            RECOMMEND_TRACKS_TOOL,
            lambda uid, inp, now: handle_recommend_tracks(uid, inp, now, music_service=music_service),
        ),
        (
            CREATE_PLAYLIST_TOOL,
            lambda uid, inp, now: handle_create_playlist(uid, inp, now, music_service=music_service),
        ),
        (
            SYNC_LIBRARY_TOOL,
            lambda uid, inp, now: handle_sync_library(uid, inp, now, music_service=music_service),
        ),
        (
            MUSIC_CONNECT_TOOL,
            lambda uid, inp, now: handle_music_connect(uid, inp, now, music_service=music_service),
        ),
    ]
