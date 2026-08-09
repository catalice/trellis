"""
Memory index — Trellis's one semantic catalogue (text -> meaning -> recall).

Every domain files uniform cards here (kind + entity id + the text + its vector);
recall runs a single cosine search across the lot. This is what makes semantic
recall Trellis-wide rather than per-table: a new kind of thing becomes searchable
just by filing its meaning here — no new column, no new query.

Embeddings come from the Embedder (see infra_embeddings). When no embedder is
configured, remember() quietly no-ops (the card just isn't filed, and the backfill
catches it up) and recall() reports itself unavailable — nothing breaks. Failures
are counted so the Telegram layer can raise a one-time alert if the pipeline is
persistently down (see take_failure_alert).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from trellis.infra_embeddings import Embedder, to_pgvector_literal

_log = logging.getLogger(__name__)

_RECALL_LIMIT = 8

# Consecutive embed failures before the pipeline looks persistently broken
# (dead token / endpoint down) rather than a one-off blip.
_FAILURE_ALERT_THRESHOLD = 3


@dataclass(frozen=True)
class SemanticMatch:
    kind: str                 # "capture" | "effort" | "seed" | ...
    entity_id: UUID
    content: str              # the text that was filed, for display
    similarity: float         # 0..1, higher = closer in meaning


class MemoryIndex:
    """The one meaning-catalogue. Depends on an Embedder (optional) + the DB."""

    def __init__(self, database: Any, embedder: Embedder | None = None) -> None:
        self._db = database
        self._embedder = embedder
        self._consecutive_failures = 0

    # -- write ----------------------------------------------------------------

    _UPSERT_SQL = """
        INSERT INTO memory_index
            (user_id, entity_kind, entity_id, content, embedding, updated_at)
        VALUES (%s, %s, %s, %s, %s::vector, NOW())
        ON CONFLICT (entity_kind, entity_id) DO UPDATE SET
            content = EXCLUDED.content,
            embedding = EXCLUDED.embedding,
            updated_at = NOW()
    """

    def remember(self, user_id: UUID, entity_kind: str, entity_id: UUID, text: str) -> bool:
        """Embed `text` and upsert its card into the index. Best-effort: no
        embedder, empty text, or a failed embed just skips filing — never raises,
        never blocks the caller's own write. Returns True if a card was filed,
        False if skipped. One embedding request per call — fine for embed-on-write
        (one save at a time); a backfill should use remember_many to batch."""
        clean = (text or "").strip()
        if self._embedder is None or not clean:
            return False
        vector = self._embed(clean)
        if vector is None:
            return False
        try:
            with self._db.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        self._UPSERT_SQL,
                        (user_id, entity_kind, entity_id, clean, to_pgvector_literal(vector)),
                    )
            return True
        except Exception:
            _log.warning("memory_index upsert failed for %s %s", entity_kind, entity_id, exc_info=True)
            return False

    def remember_many(self, items: "list[tuple[UUID, str, UUID, str]]") -> int:
        """Batch-file many cards in ONE embedder call — the local model is much
        faster embedding a batch than N single texts, which is what a backfill
        wants. items are (user_id, entity_kind, entity_id, text); returns how
        many were filed. A failed embed files none — re-run later, idempotent."""
        rows = [(u, k, e, (t or "").strip()) for (u, k, e, t) in items]
        rows = [r for r in rows if r[3]]
        if self._embedder is None or not rows:
            return 0
        try:
            vectors = self._embedder.embed([r[3] for r in rows])
        except Exception:
            _log.warning("batch embed failed", exc_info=True)
            vectors = None
        if not vectors or len(vectors) != len(rows):
            self._consecutive_failures += 1
            return 0
        self._consecutive_failures = 0
        filed = 0
        try:
            with self._db.connect() as conn:
                with conn.cursor() as cur:
                    for (user_id, entity_kind, entity_id, content), vector in zip(rows, vectors):
                        cur.execute(
                            self._UPSERT_SQL,
                            (user_id, entity_kind, entity_id, content, to_pgvector_literal(vector)),
                        )
                        filed += 1
            return filed
        except Exception:
            _log.warning("memory_index batch upsert failed", exc_info=True)
            return filed

    def forget(self, entity_kind: str, entity_id: UUID) -> None:
        """Drop an entity's card — call when the underlying thing is deleted or
        archived, so recall can't surface a stale pointer. Best-effort; forgetting
        something that was never filed is a harmless no-op."""
        try:
            with self._db.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM memory_index WHERE entity_kind = %s AND entity_id = %s",
                        (entity_kind, entity_id),
                    )
        except Exception:
            _log.warning("memory_index delete failed for %s %s", entity_kind, entity_id, exc_info=True)

    # -- read -----------------------------------------------------------------

    def recall(self, user_id: UUID, query: str, *, limit: int = _RECALL_LIMIT) -> list[SemanticMatch] | None:
        """Find the cards closest in meaning to `query`. Returns None when recall
        is unavailable (no embedder, or the query itself couldn't be embedded) so
        the caller can say so; [] means it searched and nothing was close."""
        clean = (query or "").strip()
        if self._embedder is None or not clean:
            return None
        vector = self._embed(clean)
        if vector is None:
            return None
        literal = to_pgvector_literal(vector)
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT entity_kind, entity_id, content,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM memory_index
                    WHERE user_id = %s AND embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (literal, user_id, literal, limit),
                )
                return [
                    SemanticMatch(kind=row[0], entity_id=row[1], content=row[2], similarity=float(row[3]))
                    for row in cur.fetchall()
                ]

    def theme_count(self, user_id: UUID, phrase: str, *, since: Any,
                    min_similarity: float = 0.5, limit: int = 12) -> tuple[int, list[str]]:
        """How often a THEME has recurred: cards filed since `since` whose meaning
        sits within `min_similarity` of the phrase. The Watcher's recurrence test
        — deterministic given the frozen local embedder. Returns (count, examples)."""
        clean = (phrase or "").strip()
        if self._embedder is None or not clean:
            return 0, []
        vector = self._embed(clean)
        if vector is None:
            return 0, []
        literal = to_pgvector_literal(vector)
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT content, 1 - (embedding <=> %s::vector) AS similarity
                    FROM memory_index
                    WHERE user_id = %s AND embedding IS NOT NULL AND updated_at >= %s
                      AND 1 - (embedding <=> %s::vector) >= %s
                    ORDER BY similarity DESC
                    LIMIT %s
                    """,
                    (literal, user_id, since, literal, min_similarity, limit),
                )
                rows = cur.fetchall()
        return len(rows), [r[0][:60] for r in rows[:3]]

    # -- failure tracking (for the one-time repeat-failure alert) -------------

    def _embed(self, text: str) -> list[float] | None:
        """Embed a single text, tracking consecutive failures. A blip fails one
        row; a persistent outage trips the alert threshold (see take_failure_alert)."""
        try:
            vectors = self._embedder.embed([text]) if self._embedder else None
        except Exception:
            _log.warning("embed failed", exc_info=True)
            vectors = None
        vector = vectors[0] if vectors else None
        if vector is None:
            self._consecutive_failures += 1
        else:
            self._consecutive_failures = 0
        return vector

    def take_failure_alert(self) -> bool:
        """One-shot: True at most once per failure streak, so the caller can send a
        single heads-up. Resets the streak, so it won't nag again until embeds
        recover and then fail anew. Safe to call after a turn (no concurrent turn
        is mutating the counter at that point)."""
        if self._consecutive_failures >= _FAILURE_ALERT_THRESHOLD:
            self._consecutive_failures = 0
            return True
        return False
