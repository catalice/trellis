"""
Semantic router — routes a message to relevant domains ("rooms") by MEANING, not
keywords. Each room has a self-description; we embed those once and, per message,
embed the message and pick the room(s) whose description is closest in meaning.

Big brain + little rooms: focus is the always-on core (the front door);
other rooms light up only when the message clearly means them. Mirrors
core_router.Router's interface — route(message) -> set[str] — so it's a drop-in
swap later.

Relative matching, NOT an absolute magic threshold:
  - take the clearly-best room;
  - add a second room only if its score is CLOSE to the top (ambiguous -> load both);
  - if nothing scores meaningfully, return empty (the big brain / default handles it).

Graceful: if the embedder is unavailable or errors, fall back to a provided
fallback router (e.g. the keyword Router); if none given, return all rooms (load
broadly). It never crashes — routing must never take the bot down.

The score thresholds are tuned from the offline harness's real score table
(tests/test_semantic_router.py, local bge-small): generic chat tops out around
0.46 against any room while the weakest clear room match sits around 0.50, so
min_score splits that gap — generic/ambient messages route EMPTY (the big brain
carries the turn) instead of dragging a room in. Re-run the harness with -s to
see the table whenever descriptions or thresholds change.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Protocol

_log = logging.getLogger(__name__)

# Below this top cosine score, nothing is "meaningfully" about a specialist room
# -> route empty (the big brain handles it). bge-small never scores below ~0.35
# even for unrelated text, so the floor sits just under the weakest real match.
_MIN_SCORE = 0.48
# A second room within this margin of the top is "close" -> ambiguous, load both.
_CLOSE_MARGIN = 0.04


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]] | None: ...


class Fallback(Protocol):
    def route(self, message: str) -> set[str]: ...


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SemanticRouter:
    def __init__(
        self,
        descriptions: dict[str, str],
        embedder: Embedder,
        *,
        fallback: Fallback | None = None,
        min_score: float = _MIN_SCORE,
        close_margin: float = _CLOSE_MARGIN,
    ) -> None:
        self._descriptions = dict(descriptions)
        self._embedder = embedder
        self._fallback = fallback
        self._min_score = min_score
        self._close_margin = close_margin
        self._room_vectors: dict[str, list[float]] | None = None  # embedded once, lazily

    def _ensure_room_vectors(self) -> dict[str, list[float]] | None:
        """Embed the room descriptions once and cache. None if embedding fails."""
        if self._room_vectors is not None:
            return self._room_vectors
        names = list(self._descriptions)
        if not names:
            return None
        try:
            vectors = self._embedder.embed([self._descriptions[n] for n in names])
        except Exception:
            _log.warning("SemanticRouter: room description embed failed", exc_info=True)
            return None
        if not vectors or len(vectors) != len(names):
            return None
        self._room_vectors = dict(zip(names, vectors))
        return self._room_vectors

    def scores(self, message: str) -> dict[str, float] | None:
        """Per-room cosine similarity for a message, or None if embedding is
        unavailable. Exposed so the offline harness can print the real numbers."""
        rooms = self._ensure_room_vectors()
        if rooms is None:
            return None
        clean = (message or "").strip()
        if not clean:
            return {name: 0.0 for name in rooms}
        try:
            mvec = self._embedder.embed([clean])
        except Exception:
            _log.warning("SemanticRouter: message embed failed", exc_info=True)
            return None
        if not mvec:
            return None
        m = mvec[0]
        return {name: _cosine(m, vec) for name, vec in rooms.items()}

    def route(self, message: str) -> set[str]:
        """Rooms whose meaning matches the message (possibly empty -> big brain).
        Never raises."""
        try:
            scores = self.scores(message)
        except Exception:
            _log.warning("SemanticRouter.route failed", exc_info=True)
            scores = None

        if scores is None:
            # Embedder down -> graceful fallback, never a crash.
            if self._fallback is not None:
                return self._fallback.route(message)
            return set(self._descriptions)  # load broadly rather than deny

        if not scores:
            return set()

        top_name, top_score = max(scores.items(), key=lambda kv: kv[1])
        if top_score < self._min_score:
            return set()  # nothing meaningfully about a room -> big brain / default

        matched = {top_name}
        for name, score in scores.items():
            if name != top_name and (top_score - score) <= self._close_margin:
                matched.add(name)  # close second -> ambiguous, load both
        return matched
