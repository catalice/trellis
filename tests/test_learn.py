"""Learn house — the map is theirs, sources are law, tests land on the map."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from trellis.domain_learn_models import EntryKind, LearnEntry, LearnThread
from trellis.domain_learn_service import LearnService, SourceRequiredError, _map_body
from trellis.domain_learn_tool import handle_learn_add, handle_learn_get

UID = uuid4()
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
TZ = ZoneInfo("UTC")


class FakeRepo:
    def __init__(self):
        self.threads: dict[UUID, LearnThread] = {}
        self.entries: list[LearnEntry] = []

    def save_thread(self, t):
        self.threads[t.id] = t
        return t

    def get_thread_by_title(self, user_id, title):
        return next((t for t in self.threads.values()
                     if t.title.lower() == title.strip().lower()), None)

    def list_threads(self, user_id):
        return list(self.threads.values())

    def set_position(self, user_id, thread_id, position):
        from dataclasses import replace
        if thread_id in self.threads:
            self.threads[thread_id] = replace(self.threads[thread_id], position=position)
            return True
        return False

    def save_entry(self, e):
        self.entries.append(e)
        return e

    def list_entries(self, user_id, thread_id):
        return [e for e in self.entries if e.thread_id == thread_id]


def _svc():
    return LearnService(FakeRepo(), TZ)


class TestSourceInTruth:
    def test_source_without_url_refused(self):
        svc = _svc()
        thread = svc.find_or_create_thread(UID, "Geopolitics", NOW)
        with pytest.raises(SourceRequiredError):
            svc.add_entry(UID, thread, kind=EntryKind.SOURCE,
                          content="NATO expanded eastward", now=NOW)

    def test_source_with_url_kept(self):
        svc = _svc()
        thread = svc.find_or_create_thread(UID, "Geopolitics", NOW)
        e = svc.add_entry(UID, thread, kind=EntryKind.SOURCE,
                          content="NATO expansion timeline",
                          source_url="https://example.org/nato",
                          source_title="Example History", now=NOW)
        assert e.source_url == "https://example.org/nato"

    def test_tool_teaches_when_source_missing(self):
        svc = _svc()
        svc.find_or_create_thread(UID, "Geopolitics", NOW)
        reply = handle_learn_add(
            UID, {"what": "entry", "thread": "Geopolitics", "kind": "source",
                  "content": "a claim"}, NOW, learn_service=svc)
        assert "source_url" in reply


class TestTheMapIsTheirs:
    def test_thread_title_reused_not_duplicated(self):
        svc = _svc()
        a = svc.find_or_create_thread(UID, "Geopolitics", NOW)
        b = svc.find_or_create_thread(UID, "geopolitics", NOW)
        assert a.id == b.id

    def test_unplaced_entry_prompts_for_region(self):
        svc = _svc()
        svc.find_or_create_thread(UID, "Geopolitics", NOW)
        reply = handle_learn_add(
            UID, {"what": "entry", "thread": "Geopolitics",
                  "content": "states act on interests"}, NOW, learn_service=svc)
        assert "ask them where it fits" in reply

    def test_position_flows_to_reads(self):
        svc = _svc()
        svc.find_or_create_thread(UID, "Geopolitics", NOW)
        handle_learn_add(
            UID, {"what": "position", "thread": "Geopolitics",
                  "position": "foundations: states and interests"},
            NOW, learn_service=svc)
        listing = handle_learn_get(UID, {"what": "threads"}, NOW, learn_service=svc)
        assert "you are here: foundations" in listing


class TestMapPage:
    def test_regions_group_and_tests_sink(self):
        thread = LearnThread(id=uuid4(), user_id=UID, title="Geopolitics",
                             position="foundations")
        entries = [
            LearnEntry(id=uuid4(), user_id=UID, thread_id=thread.id,
                       kind=EntryKind.MATERIAL, content="States act on interests",
                       region="Foundations"),
            LearnEntry(id=uuid4(), user_id=UID, thread_id=thread.id,
                       kind=EntryKind.SOURCE, content="Interests primer",
                       region="Foundations", source_url="https://x.org/p",
                       source_title="Primer"),
            LearnEntry(id=uuid4(), user_id=UID, thread_id=thread.id,
                       kind=EntryKind.TEST, content="Q: why do states ally? — solid"),
        ]
        body = _map_body(thread, entries, TZ)
        assert "**You are here:** foundations" in body
        assert "## Foundations" in body
        assert "[Primer](https://x.org/p)" in body
        assert body.index("## Test history") > body.index("## Foundations")
