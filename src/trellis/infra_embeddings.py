"""
Embeddings — Trellis's semantic memory (text -> vector).

Swappable by design: everything depends on the Embedder protocol, so switching
provider is one new class here plus a one-line change wherever the embedder is
constructed. Nothing else in Trellis knows or cares who embeds.

Current implementation: LOCAL, via fastembed (ONNX) — model BAAI/bge-small-en-v1.5
(384 dims, L2-normalised). Runs in-process: no API key, no rate limits, no network,
nothing that can be retired out from under us. (The previous cloud embedder,
GitHub Models, was retired on 30 Jul 2026 — hence the move local.) The model file
(~130MB) is downloaded once and cached; in Docker it's baked into the image so the
bot works offline at runtime.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

_log = logging.getLogger(__name__)

_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_DIMENSIONS = 384


def to_pgvector_literal(vector: Sequence[float]) -> str:
    """Format an embedding as a pgvector text literal, for binding into a
    `%s::vector` placeholder. One definition shared by every writer/searcher."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]] | None: ...


class LocalEmbedder:
    """Local ONNX embedder (fastembed / bge-small). The model loads lazily on the
    first embed and is cached for the process's life. Best-effort: any failure
    logs and returns None so callers degrade gracefully rather than crash."""

    _model = None  # class-level cache: the model loads once per process

    def __init__(self, model_name: str = _MODEL_NAME) -> None:
        self._model_name = model_name

    def _ensure_model(self):
        if LocalEmbedder._model is None:
            from fastembed import TextEmbedding  # imported lazily — heavy-ish

            _log.info("Loading local embedding model %s", self._model_name)
            LocalEmbedder._model = TextEmbedding(self._model_name)
        return LocalEmbedder._model

    def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        items = list(texts)
        if not items:
            return []
        try:
            model = self._ensure_model()
            vectors = [
                [float(x) for x in vec]  # numpy array -> plain floats
                for vec in model.embed(items)
            ]
        except Exception:
            _log.warning("LocalEmbedder failed for %d texts", len(items), exc_info=True)
            return None

        if len(vectors) != len(items) or any(len(v) != _DIMENSIONS for v in vectors):
            _log.warning("LocalEmbedder returned malformed vectors")
            return None
        return vectors
