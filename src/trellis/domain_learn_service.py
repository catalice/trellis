"""Learn service — thin. The teaching happens in the oracle turn (guidance in
domain_learn_claude); this persists threads/entries, enforces source-in-truth,
and projects each thread's map into the vault. Typed data only."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, tzinfo
from typing import Protocol
from uuid import UUID, uuid4

from trellis.domain_learn_models import EntryKind, LearnEntry, LearnThread
from trellis.domain_learn_repo import LearnRepository

_log = logging.getLogger(__name__)


class MapProjection(Protocol):
    """Write-only vault view: one map page per thread. Must never raise."""
    def learn_map(self, title: str, body: str) -> None: ...


class SourceRequiredError(ValueError):
    """kind='source' without a URL — a reference that can't be followed back
    to its source doesn't get kept (source-in-truth, CLAUDE.md)."""


class LearnService:
    def __init__(
        self,
        repo: LearnRepository,
        tz: tzinfo,
        projection: MapProjection | None = None,
    ) -> None:
        self._repo = repo
        self._tz = tz
        self._projection = projection

    def find_or_create_thread(self, user_id: UUID, title: str, now: datetime) -> LearnThread:
        existing = self._repo.get_thread_by_title(user_id, title)
        if existing:
            return existing
        thread = self._repo.save_thread(LearnThread(
            id=uuid4(), user_id=user_id, title=title.strip(),
            created_at=now, updated_at=now,
        ))
        self._project(user_id, thread)
        return thread

    def list_threads(self, user_id: UUID) -> list[LearnThread]:
        return self._repo.list_threads(user_id)

    def entries(self, user_id: UUID, thread: LearnThread) -> list[LearnEntry]:
        return self._repo.list_entries(user_id, thread.id)

    def add_entry(
        self,
        user_id: UUID,
        thread: LearnThread,
        *,
        kind: EntryKind,
        content: str,
        region: str | None = None,
        source_url: str | None = None,
        source_title: str | None = None,
        now: datetime,
    ) -> LearnEntry:
        if kind == EntryKind.SOURCE and not (source_url or "").strip():
            raise SourceRequiredError("a kept reference must carry its source_url")
        entry = self._repo.save_entry(LearnEntry(
            id=uuid4(), user_id=user_id, thread_id=thread.id, kind=kind,
            content=content.strip(), region=(region or "").strip() or None,
            source_url=(source_url or "").strip() or None,
            source_title=(source_title or "").strip() or None,
            created_at=now,
        ))
        self._project(user_id, thread)
        return entry

    def set_position(self, user_id: UUID, thread: LearnThread, position: str) -> bool:
        ok = self._repo.set_position(user_id, thread.id, position.strip())
        if ok:
            self._project(user_id, thread)
        return ok

    # -- vault map page (write-only, never raises) ----------------------------

    def _project(self, user_id: UUID, thread: LearnThread) -> None:
        if self._projection is None:
            return
        try:
            fresh = self._repo.get_thread_by_title(user_id, thread.title) or thread
            entries = self._repo.list_entries(user_id, thread.id)
            self._projection.learn_map(thread.title, _map_body(fresh, entries, self._tz))
        except Exception:
            _log.warning("learn map projection failed", exc_info=True)


def _map_body(thread: LearnThread, entries: list[LearnEntry], tz: tzinfo) -> str:
    """The map as a page: position first, then regions in the order they were
    first drawn, sources cited inline, test history at the bottom."""
    lines: list[str] = []
    lines.append(f"*Updated {datetime.now(tz).strftime('%a %-d %B %Y')}*\n")
    if thread.position:
        lines.append(f"**You are here:** {thread.position}\n")
    regions: dict[str, list[LearnEntry]] = {}
    order: list[str] = []
    tests: list[LearnEntry] = []
    for e in entries:
        if e.kind == EntryKind.TEST:
            tests.append(e)
            continue
        key = e.region or "Unplaced"
        if key not in regions:
            regions[key] = []
            order.append(key)
        regions[key].append(e)
    for region in order:
        lines.append(f"## {region}")
        for e in regions[region]:
            src = ""
            if e.source_url:
                label = e.source_title or "source"
                src = f" — [{label}]({e.source_url})"
            lines.append(f"- {e.content}{src}")
        lines.append("")
    if tests:
        lines.append("## Test history")
        for e in tests:
            lines.append(f"- {e.created_at.astimezone(tz).strftime('%-d %b')}: {e.content}")
        lines.append("")
    if not entries:
        lines.append("*Nothing placed yet — the map grows as you do.*")
    return "\n".join(lines)
