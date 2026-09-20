"""Fragments are for finding things; the whole record is fetched by its id.
Someone saves a long argument and later asks about a detail near the end — the
text was stored, and the model had no way to read it. And 'nothing there' is
only ever said about the window that was actually looked at."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

from trellis.domain_focus_models import Capture, CaptureType
from trellis.domain_focus_tool import _FocusReads, handle_focus_get, handle_recall

TZ = ZoneInfo("UTC")
NOW = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
UID = uuid4()
LONG = " ".join(f"sentence {n} of the argument." for n in range(1, 801))        # ~24k characters
TAIL = "sentence 800 of the argument."


def _capture(raw=LONG, days_ago=1, summary="The long argument"):
    return Capture(id=uuid4(), user_id=UID, raw=raw, capture_type=CaptureType.IDEA, synthesis=None,
                   summary=summary, effort_id=None, created_at=NOW - timedelta(days=days_ago))


class _Captures:
    def __init__(self, captures): self._all = list(captures)
    def get(self, user_id, capture_id): return next((c for c in self._all if c.id == capture_id), None)
    def list_unassigned(self, user_id, *, days=30):
        return [c for c in self._all if c.created_at >= NOW - timedelta(days=days)]
    def count_unassigned_older_than(self, user_id, *, days=30):
        return len([c for c in self._all if c.created_at < NOW - timedelta(days=days)])
    def for_effort(self, user_id, effort_id): return list(self._all)


def _ctx(captures, effort=None):
    efforts = SimpleNamespace(page=lambda uid, title, caps: (effort, caps.for_effort(uid, effort.id)) if effort else None)
    return dict(task_service=None, goal_service=None, capture_service=_Captures(captures),
                effort_service=efforts, reminder_service=None, tz=TZ)


def _get(input_dict, captures, effort=None):
    return handle_focus_get(UID, input_dict, NOW, **_ctx(captures, effort))


class TestAWholeRecordCanBeRead:
    def test_a_capture_is_fetched_in_full_across_pages(self):
        capture = _capture()
        first = _get({"what": "capture", "id": str(capture.id)}, [capture])
        assert "sentence 1 of the argument." in first and "page 1 of" in first
        import re
        pages = int(re.search(r"page 1 of (\d+)", first).group(1))
        last = _get({"what": "capture", "id": str(capture.id), "page": pages}, [capture])
        assert TAIL in last                                             # the detail near the end is reachable
        everything = "".join(_get({"what": "capture", "id": str(capture.id), "page": n}, [capture])
                             for n in range(1, pages + 1))
        assert all(f"sentence {n} of the argument." in everything for n in (1, 400, 800))

    def test_a_short_capture_comes_back_whole_with_no_paging_noise(self):
        capture = _capture(raw="buy quince")
        out = _get({"what": "capture", "id": str(capture.id)}, [capture])
        assert "buy quince" in out and "page" not in out.lower()

    def test_an_unknown_id_and_a_page_out_of_range_are_said(self):
        capture = _capture()
        assert "No capture" in _get({"what": "capture", "id": str(uuid4())}, [capture])
        assert "has " in _get({"what": "capture", "id": str(capture.id), "page": 999}, [capture])


class TestAFragmentSaysItIsOne:
    def test_an_effort_page_marks_cut_notes_and_says_how_to_read_the_rest(self):
        capture = _capture()
        effort = SimpleNamespace(id=uuid4(), title="Essay", intensity=SimpleNamespace(value="active"), notes=None)
        out = _get({"what": "effort", "name": "Essay"}, [capture], effort)
        assert TAIL not in out
        assert str(capture.id) in out and "what='capture'" in out and "of " in out

    def test_recall_marks_cut_matches_and_says_how_to_read_the_rest(self):
        match = SimpleNamespace(entity_id=uuid4(), kind="capture", similarity=0.81, content=LONG)
        out = handle_recall(UID, {"query": "the argument"}, NOW, memory=SimpleNamespace(recall=lambda uid, q: [match]))
        assert str(match.entity_id) in out and "what='capture'" in out


class TestAbsenceIsSaidWithItsScope:
    def test_an_empty_recent_inbox_with_older_captures_is_not_called_clear(self):
        old = _capture(raw="an old thought", days_ago=45, summary="Old")
        out = _get({"what": "inbox"}, [old])
        assert "clear" not in out.lower()
        assert "30 days" in out and "1 older" in out

    def test_a_truly_empty_inbox_says_so_with_its_window(self):
        out = _get({"what": "inbox"}, [])
        assert "30 days" in out and "older" not in out


class TestALearnMapCanBeReadInFull:
    """The map promised 'in full' and cut every entry at 160 characters, with no
    way to read the rest; a source showed its title and hid its URL."""

    def _service(self, entries, thread):
        return SimpleNamespace(list_threads=lambda uid: [thread], entries=lambda uid, t: entries)

    def _entry(self, content, **kw):
        from trellis.domain_learn_models import EntryKind, LearnEntry
        return LearnEntry(id=uuid4(), user_id=UID, thread_id=uuid4(), kind=kw.pop("kind", EntryKind.MATERIAL),
                          content=content, **kw)

    def test_a_cut_entry_says_so_and_the_whole_of_it_can_be_read(self):
        from trellis.domain_learn_tool import handle_learn_get
        entry = self._entry(LONG, region="Origins")
        thread = SimpleNamespace(id=uuid4(), title="Rome", position=None)
        svc = self._service([entry], thread)
        listing = handle_learn_get(UID, {"what": "map", "thread": "Rome"}, NOW, learn_service=svc)
        assert TAIL not in listing and str(entry.id) in listing and "what='entry'" in listing
        import re
        first = handle_learn_get(UID, {"what": "entry", "thread": "Rome", "id": str(entry.id)}, NOW, learn_service=svc)
        pages = int(re.search(r"page 1 of (\d+)", first).group(1))
        last = handle_learn_get(UID, {"what": "entry", "thread": "Rome", "id": str(entry.id), "page": pages},
                                NOW, learn_service=svc)
        assert TAIL in last

    def test_a_source_always_shows_its_url(self):
        from trellis.domain_learn_models import EntryKind
        from trellis.domain_learn_tool import handle_learn_get
        entry = self._entry("On the grain dole.", kind=EntryKind.SOURCE, source_title="The Grain Dole",
                            source_url="https://example.org/grain")
        thread = SimpleNamespace(id=uuid4(), title="Rome", position=None)
        out = handle_learn_get(UID, {"what": "map", "thread": "Rome"}, NOW, learn_service=self._service([entry], thread))
        assert "The Grain Dole" in out and "https://example.org/grain" in out
