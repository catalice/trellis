"""
Embeddings — Trellis's semantic memory (text -> vector).

Swappable by design: everything depends on the Embedder protocol, so switching
provider (Azure OpenAI direct, a local model) is one new class here plus a
one-line change wherever the embedder is constructed. Nothing else in Trellis
knows or cares who embeds.

Current implementation: GitHub Models (https://models.inference.ai.azure.com) —
the OpenAI-compatible embeddings endpoint, model text-embedding-3-small (1536
dims). Auth is a GitHub token (GITHUB_TOKEN). The free tier caps *requests*
(15/min, 150/day), not size (~64k tokens/request), so one call = one request and
bulk writers batch by token budget (see infra_memory.remember_many).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

import httpx

_log = logging.getLogger(__name__)

_EMBEDDINGS_URL = "https://models.inference.ai.azure.com/embeddings"
_MODEL = "text-embedding-3-small"
_DIMENSIONS = 1536
_TIMEOUT = 30.0


def to_pgvector_literal(vector: Sequence[float]) -> str:
    """Format an embedding as a pgvector text literal, for binding into a
    `%s::vector` placeholder. One definition shared by every writer/searcher."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]] | None: ...


class GitHubModelsEmbedder:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        """One embedding request for the given texts. The free tier caps *requests*,
        not size (~64k tokens/request), so callers batch by token budget and keep the
        request count low. Returns None on failure (never raises)."""
        if not self._api_key:
            return None
        batch = list(texts)
        if not batch:
            return []
        return self._embed_batch(batch)

    def _embed_batch(self, batch: list[str]) -> list[list[float]] | None:
        try:
            response = httpx.post(
                _EMBEDDINGS_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": _MODEL, "input": batch},
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            _log.warning(
                "GitHubModelsEmbedder failed for %d texts", len(batch), exc_info=True
            )
            return None

        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != len(batch):
            _log.warning(
                "GitHubModelsEmbedder returned %s rows for %d texts",
                len(rows) if isinstance(rows, list) else "no",
                len(batch),
            )
            return None

        # Response order isn't guaranteed — reorder by the index echoed back.
        ordered = sorted(rows, key=lambda r: r.get("index", 0))
        vectors = [r.get("embedding") for r in ordered]
        if any(not isinstance(v, list) or len(v) != _DIMENSIONS for v in vectors):
            _log.warning("GitHubModelsEmbedder returned malformed vectors")
            return None
        return vectors
