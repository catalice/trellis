"""
Semantic router — routes a message to relevant domains by MEANING, not keywords.

Houses and rooms: each domain is a HOUSE (move, sense, focus), described by the
ROOMS inside it — short concrete phrases for the kinds of things it handles
("shopping lists", "recovery and readiness", "intervals and long runs"). Every
room is embedded once; per message we embed the message and score each house by
its BEST-matching room. Scaling a house = adding a room to its list — the router
picks it up automatically, no other change.

Why best-room and not one blended description per house: a single door-label
vector dilutes strong signals ("add milk to my shopping list" scores 0.81
against the room "shopping lists" but only 0.52 against focus's blended
sentence, which put it within noise of other houses). Max-over-rooms keeps the
sharp match sharp, and makes routing explainable: the route happened because of
a specific room.

Relative matching, NOT an absolute magic threshold:
  - take the clearly-best house;
  - add a second house only if its score is CLOSE to the top (ambiguous -> both);
  - if nothing beats the floor, return empty — the big brain (always-on core)
    carries the turn. Generic chat lands there by design.

The floor is tuned from the offline harness's real score table
(tests/test_semantic_router.py, local bge-small): generic chat tops out around
0.61 against any room while the weakest clear match sits around 0.66, so
min_score splits that gap. Re-run the harness with -s to see the table whenever
rooms or thresholds change.

Graceful: if the embedder is unavailable or errors, fall back to a provided
fallback router (e.g. the keyword Router); if none given, return all houses
(load broadly). It never crashes — routing must never take the bot down.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Protocol

_log = logging.getLogger(__name__)

# Below this top best-room score, nothing is meaningfully about a specialist
# house -> route empty (the big brain handles it). bge-small scores ~0.5 for
# ANY two bits of everyday English, so the floor sits just under the weakest
# real match, well above that noise.
_MIN_SCORE = 0.635
# A second house within this margin of the top is "close" -> ambiguous, load both.
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
        houses: dict[str, list[str]],
        embedder: Embedder,
        *,
        fallback: Fallback | None = None,
        min_score: float = _MIN_SCORE,
        close_margin: float = _CLOSE_MARGIN,
    ) -> None:
        self._houses = {house: list(rooms) for house, rooms in houses.items() if rooms}
        self._embedder = embedder
        self._fallback = fallback
        self._min_score = min_score
        self._close_margin = close_margin
        # {house: [(room, vector), ...]} — embedded once, lazily
        self._room_vectors: dict[str, list[tuple[str, list[float]]]] | None = None

    def _ensure_room_vectors(self) -> dict[str, list[tuple[str, list[float]]]] | None:
        """Embed every house's rooms once (one batched call) and cache.
        None if embedding fails."""
        if self._room_vectors is not None:
            return self._room_vectors
        pairs = [(house, room) for house, rooms in self._houses.items() for room in rooms]
        if not pairs:
            return None
        try:
            vectors = self._embedder.embed([room for _, room in pairs])
        except Exception:
            _log.warning("SemanticRouter: room embed failed", exc_info=True)
            return None
        if not vectors or len(vectors) != len(pairs):
            return None
        by_house: dict[str, list[tuple[str, list[float]]]] = {h: [] for h in self._houses}
        for (house, room), vector in zip(pairs, vectors):
            by_house[house].append((room, vector))
        self._room_vectors = by_house
        return self._room_vectors

    def explain(self, message: str) -> dict[str, tuple[float, str]] | None:
        """Per-house (best score, best-matching room) for a message, or None if
        embedding is unavailable. The room names make routing explainable; the
        offline harness prints them."""
        houses = self._ensure_room_vectors()
        if houses is None:
            return None
        clean = (message or "").strip()
        if not clean:
            return {house: (0.0, "") for house in houses}
        try:
            mvec = self._embedder.embed([clean])
        except Exception:
            _log.warning("SemanticRouter: message embed failed", exc_info=True)
            return None
        if not mvec:
            return None
        m = mvec[0]
        return {
            house: max(((_cosine(m, vec), room) for room, vec in rooms))
            for house, rooms in houses.items()
        }

    def scores(self, message: str) -> dict[str, float] | None:
        """Per-house best-room score for a message, or None if embedding is
        unavailable."""
        detail = self.explain(message)
        if detail is None:
            return None
        return {house: score for house, (score, _room) in detail.items()}

    def route(self, message: str) -> set[str]:
        """Houses whose meaning matches the message (possibly empty -> big brain).
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
            return set(self._houses)  # load broadly rather than deny

        if not scores:
            return set()

        top_name, top_score = max(scores.items(), key=lambda kv: kv[1])
        if top_score < self._min_score:
            return set()  # nothing meaningfully about a house -> big brain

        matched = {top_name}
        for name, score in scores.items():
            if name != top_name and (top_score - score) <= self._close_margin:
                matched.add(name)  # close second -> ambiguous, load both
        return matched
