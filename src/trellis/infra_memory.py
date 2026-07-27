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

# Bulk embedding batches by token budget: one request holds ~64k tokens, so a
# batch is capped by an approximate char budget (~4 chars/token) and an item
# count. Hundreds of short rows (e.g. tracks) then cost a single request.
_BATCH_CHAR_BUDGET = 200_000
_BATCH_ITEM_CAP = 512


def _batches(rows: list) -> "list[list]":
    """Split rows into embed-batches respecting the char + item caps. Each row is
    (user_id, entity_kind, entity_id, text); batching is by text length."""
    out: list[list] = []
    chunk: list = []
    chars = 0
    for row in rows:
        length = len(row[3])
        if chunk and (len(chunk) >= _BATCH_ITEM_CAP or chars + length > _BATCH_CHAR_BUDGET):
            out.append(chunk)
            chunk, chars = [], 0
        chunk.append(row)
        chars += length
    if chunk:
        out.append(chunk)
    return out


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
        """Batch-file many cards in as few embedding requests as possible. The free
        tier caps *requests* (15/min, 150/day), not size (~64k tokens/request), so
        rows are chunked by token budget — hundreds of short rows fit in one request.
        Each chunk's successes are stored before the next, so a rate-limit mid-run
        just leaves the rest for a re-run (idempotent upsert). items are
        (user_id, entity_kind, entity_id, text); returns how many were filed."""
        rows = [(u, k, e, (t or "").strip()) for (u, k, e, t) in items]
        rows = [r for r in rows if r[3]]
        if self._embedder is None or not rows:
            return 0
        filed = 0
        for chunk in _batches(rows):
            try:
                vectors = self._embedder.embed([r[3] for r in chunk])
            except Exception:
                _log.warning("batch embed failed", exc_info=True)
                vectors = None
            if not vectors or len(vectors) != len(chunk):
                self._consecutive_failures += 1
                break  # rate-limited / failed — stop; a re-run fills the rest
            self._consecutive_failures = 0
            try:
                with self._db.connect() as conn:
                    with conn.cursor() as cur:
                        for (user_id, entity_kind, entity_id, content), vector in zip(chunk, vectors):
                            cur.execute(
                                self._UPSERT_SQL,
                                (user_id, entity_kind, entity_id, content, to_pgvector_literal(vector)),
                            )
                filed += len(chunk)
            except Exception:
                _log.warning("memory_index batch upsert failed", exc_info=True)
                break
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
