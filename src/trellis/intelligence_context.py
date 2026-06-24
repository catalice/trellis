"""
Context loader for the intelligence cross-layer.

Always loaded every turn. Surfaces active pattern insights so the oracle
has awareness of detected patterns without needing to query them via tool.

Usage in main.py:
    from trellis.intelligence_context import intelligence_context_loader
    intelligence=("intelligence", intelligence_context_loader(insight_repository)),
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol
from uuid import UUID

from trellis.registry import ContextLoader

_log = logging.getLogger(__name__)


class _InsightRepo(Protocol):
    def list_active(self, user_id: UUID) -> list: ...


def intelligence_context_loader(insight_repository: _InsightRepo) -> ContextLoader:
    def loader(user_id: UUID, now: datetime) -> str | None:
        try:
            insights = insight_repository.list_active(user_id)
        except Exception:
            _log.warning("intelligence_context: failed to load insights", exc_info=True)
            return None

        if not insights:
            return None

        lines = ["Detected patterns:"]
        for insight in insights:
            conf_pct = int(insight.confidence * 100)
            lines.append(
                f"  [{insight.domain}] {insight.summary} "
                f"(confidence {conf_pct}%, {insight.evidence_count} observations)"
            )
        return "[Intelligence]\n" + "\n".join(lines)

    return loader
