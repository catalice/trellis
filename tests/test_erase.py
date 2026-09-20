"""'Erased' means: the record, its search entry, and the text Trellis put in the
vault are gone — and what is still held elsewhere is SAID. Anything written by
hand in the vault is never touched."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from trellis.domain_focus_models import Capture, CaptureType, Effort, EffortIntensity
from trellis.infra_obsidian import ObsidianVault

NOW = datetime(2026, 3, 10, 9, 30, tzinfo=timezone.utc)


class _Efforts:
    def __init__(self, efforts=()):
        self._by_id = {e.id: e for e in efforts}
    def get(self, effort_id):
        return self._by_id.get(effort_id)


def _capture(raw="the secret thing I should not have said", summary="A private thought", effort_id=None,
             synthesis="Cleaned up: a private thought."):
    return Capture(id=uuid4(), user_id=uuid4(), raw=raw, capture_type=CaptureType.IDEA,
                   synthesis=synthesis, summary=summary, effort_id=effort_id, created_at=NOW)


def _vault(tmp_path, efforts=()):
    return ObsidianVault(tmp_path, timezone.utc, None, None, _Efforts(efforts))


class TestACaptureIsErasedFromTheVault:
    def test_its_block_leaves_the_daily_note_and_the_rest_of_the_day_stays(self, tmp_path):
        vault, keep, erase = _vault(tmp_path), _capture(raw="buy quince", summary="Shopping", synthesis="Buy quince."), _capture()
        vault.capture_saved(keep)
        vault.capture_saved(erase)
        left = vault.capture_erased(erase)
        note = (tmp_path / "Calendar/Captures/2026-03-10.md").read_text()
        assert left == []
        assert "secret thing" not in note and "A private thought" not in note
        assert "buy quince" in note and note.startswith("# Tuesday 10 March 2026")

    def test_writing_done_by_hand_beside_it_is_untouched(self, tmp_path):
        vault, erase = _vault(tmp_path), _capture()
        vault.capture_saved(erase)
        page = tmp_path / "Calendar/Captures/2026-03-10.md"
        page.write_text(page.read_text() + "\nMy own afterthought, typed in Obsidian.\n")
        vault.capture_erased(erase)
        assert "My own afterthought" in page.read_text() and "secret thing" not in page.read_text()

    def test_a_block_edited_by_hand_is_left_and_reported(self, tmp_path):
        vault, erase = _vault(tmp_path), _capture()
        vault.capture_saved(erase)
        page = tmp_path / "Calendar/Captures/2026-03-10.md"
        page.write_text(page.read_text().replace("Cleaned up: a private thought.", "Cleaned up — and I reworded this."))
        left = vault.capture_erased(erase)
        assert left and "2026-03-10" in left[0]
        assert "secret thing" in page.read_text()            # not removed: it isn't what Trellis wrote any more

    def test_its_line_and_its_research_leave_the_effort_page(self, tmp_path):
        effort = Effort(id=uuid4(), user_id=uuid4(), title="Garden", intensity=EffortIntensity.ACTIVE,
                        notes=None, obsidian_path="Efforts/Garden.md")
        vault = _vault(tmp_path, [effort])
        filed = _capture(effort_id=effort.id)
        research = _capture(raw="long research text", summary="Soil", synthesis="Soil wants lime.", effort_id=effort.id)
        vault.capture_assigned(filed)
        vault.research_saved(research)
        page = tmp_path / "Efforts/Garden.md"
        page.write_text(page.read_text() + "\nHand-written: order the quince in October.\n")
        assert vault.capture_erased(filed) == [] and vault.capture_erased(research) == []
        text = page.read_text()
        assert "A private thought" not in text and "Cleaned up: a private thought." not in text
        assert "Hand-written: order the quince" in text


class TestEraseSaysWhatItDidAndWhatRemains:
    class _Repo:
        def __init__(self, capture): self.capture, self.deleted = capture, []
        def get(self, user_id, capture_id): return self.capture if capture_id == self.capture.id else None
        def delete(self, user_id, capture_id):
            self.deleted.append(capture_id)
            return capture_id == self.capture.id

    class _Memory:
        def __init__(self): self.forgot = []
        def forget(self, kind, entity_id): self.forgot.append((kind, entity_id))

    def test_record_search_entry_and_vault_text_all_go(self, tmp_path):
        from trellis.domain_focus_service import CaptureService
        capture, memory, vault = _capture(), self._Memory(), _vault(tmp_path)
        vault.capture_saved(capture)
        svc = CaptureService(self._Repo(capture), projection=vault, memory=memory)
        result = svc.erase(capture.user_id, capture.id)
        assert result.erased and result.left_in_vault == ()
        assert memory.forgot == [("capture", capture.id)]
        assert "secret thing" not in (tmp_path / "Calendar/Captures/2026-03-10.md").read_text()

    def test_the_reply_discloses_what_trellis_still_holds(self):
        from trellis.core_actions import Status, status_of
        from trellis.domain_focus_tool import _erased_message
        from trellis.domain_focus_service import Erased
        clean = _erased_message(Erased(erased=True))
        assert status_of(clean) is Status.SUCCEEDED
        for still_held in ("conversation", "backup", "action log"):
            assert still_held in clean.lower()
        partial = _erased_message(Erased(erased=True, left_in_vault=("Calendar/Captures/2026-03-10.md",)))
        assert status_of(partial) is Status.PARTIAL and "2026-03-10" in partial


class TestATrackingEntryIsErasedFromTheMonthItWasIn:
    """Deleting an old entry refreshed only the CURRENT month, and an emptied
    month returned early — so the erased words stayed on its History page."""

    class _States:
        def __init__(self, states): self.states = list(states)
        def list_states_since(self, user_id, *, since): return [s for s in self.states if s.felt_at >= since]
        def list_events_since(self, user_id, *, since): return []
        def last_period_start(self, user_id): return None

    def _state(self, when, note):
        from trellis.domain_sense_models import StateLog
        return StateLog(id=uuid4(), user_id=uuid4(), note=note, energy=2, mood=2, felt_at=when, logged_at=when)

    def test_the_affected_months_page_is_rewritten_without_it(self, tmp_path):
        january = datetime(2026, 1, 12, 9, 0, tzinfo=timezone.utc)
        keep, erase = self._state(january, "slept fine"), self._state(january, "the thing I regret writing")
        states = self._States([keep, erase])
        vault = ObsidianVault(tmp_path, timezone.utc, None, None, None, state_repo=states)
        vault.write_tracking_month(keep.user_id, 2026, 1)
        page = tmp_path / "Calendar/Tracking/History/2026-01.md"
        assert "regret" in page.read_text()
        states.states.remove(erase)
        vault.tracking_entry_erased(keep.user_id, january.date())
        assert "regret" not in page.read_text() and "slept fine" in page.read_text()

    def test_a_month_left_empty_is_cleared_not_skipped(self, tmp_path):
        january = datetime(2026, 1, 12, 9, 0, tzinfo=timezone.utc)
        only = self._state(january, "the thing I regret writing")
        states = self._States([only])
        vault = ObsidianVault(tmp_path, timezone.utc, None, None, None, state_repo=states)
        vault.write_tracking_month(only.user_id, 2026, 1)
        states.states.clear()
        vault.tracking_entry_erased(only.user_id, january.date())
        page = tmp_path / "Calendar/Tracking/History/2026-01.md"
        assert "regret" not in page.read_text()

    def test_the_service_erases_from_the_day_the_entry_was_felt(self):
        from zoneinfo import ZoneInfo
        from trellis.domain_sense_service import SenseService
        january = datetime(2026, 1, 12, 9, 0, tzinfo=timezone.utc)
        entry_id, seen = uuid4(), []

        class Repo:
            def entry_day(self, user_id, eid): return january if eid == entry_id else None
            def delete_state(self, user_id, eid): return eid == entry_id
            def delete_event(self, user_id, eid): return False

        class Projection:
            def tracking_entry_erased(self, user_id, day): seen.append(day)
            def tracking_changed(self, user_id): seen.append("current")

        assert SenseService(Repo(), ZoneInfo("UTC"), projection=Projection()).delete_entry(uuid4(), entry_id)
        assert seen == [january.date()]


class TestEraseNeverClaimsMoreThanItDid:
    """The receipt carries each store's own result. Where removal failed, or
    can't be established, the erase is PARTIAL and says what remains or is
    uncertain — it never reports a clean erase it cannot vouch for."""

    def test_a_search_entry_that_could_not_be_removed_is_said(self, tmp_path):
        from trellis.core_actions import Status, status_of
        from trellis.domain_focus_service import CaptureService
        from trellis.domain_focus_tool import _erased_message

        class IndexDown:
            def forget(self, kind, entity_id): return False        # the delete failed

        capture = _capture()
        vault = _vault(tmp_path)
        vault.capture_saved(capture)
        result = CaptureService(TestEraseSaysWhatItDidAndWhatRemains._Repo(capture),
                                projection=vault, memory=IndexDown()).erase(capture.user_id, capture.id)
        assert result.erased and result.uncertain == ("the search index",)
        message = _erased_message(result)
        assert status_of(message) is Status.PARTIAL
        assert "search" in message.lower() and "its search entry, and" not in message

    def test_the_real_index_reports_a_failed_delete(self):
        from trellis.infra_memory import MemoryIndex

        class Down:
            def connect(self): raise ConnectionError("database is down")

        assert MemoryIndex(Down(), embedder=None).forget("capture", uuid4()) is False

    def test_an_edited_multiline_capture_is_detected_as_left_behind(self, tmp_path):
        """Raw lines are written as '> line', so searching for the raw text as
        one string missed a block that was still there."""
        capture = _capture(raw="first line of the thing\nsecond line I regret\nthird line")
        vault = _vault(tmp_path)
        vault.capture_saved(capture)
        page = tmp_path / "Calendar/Captures/2026-03-10.md"
        page.write_text(page.read_text().replace("Cleaned up: a private thought.", "Reworded by hand."))
        left = vault.capture_erased(capture)
        assert "second line I regret" in page.read_text()
        assert left == ["Calendar/Captures/2026-03-10.md"]

    def test_a_tracking_page_that_could_not_be_rewritten_makes_the_erase_partial(self, tmp_path, monkeypatch):
        from zoneinfo import ZoneInfo
        from trellis.core_actions import Status, status_of
        from trellis.domain_focus_tool import _erased_message
        from trellis.domain_sense_service import SenseService
        january = datetime(2026, 1, 12, 9, 0, tzinfo=timezone.utc)
        entry = TestATrackingEntryIsErasedFromTheMonthItWasIn()._state(january, "the thing I regret writing")
        states = TestATrackingEntryIsErasedFromTheMonthItWasIn._States([entry])
        states.entry_day = lambda user_id, eid: january
        states.delete_state = lambda user_id, eid: bool(states.states.clear() or True)
        states.delete_event = lambda user_id, eid: False
        vault = ObsidianVault(tmp_path, timezone.utc, None, None, None, state_repo=states)
        vault.write_tracking_month(entry.user_id, 2026, 1)

        import trellis.infra_obsidian as obsidian
        def disk_full(path, text): raise OSError(28, "No space left on device")
        monkeypatch.setattr(obsidian, "_write_atomically", disk_full)

        result = SenseService(states, ZoneInfo("UTC"), projection=vault).erase_entry(entry.user_id, entry.id)
        assert result.erased and any("2026-01" in page for page in result.uncertain)
        message = _erased_message(result)
        assert status_of(message) is Status.PARTIAL and "2026-01" in message
        assert "regret" in (tmp_path / "Calendar/Tracking/History/2026-01.md").read_text()   # it really is still there

    def test_a_task_whose_search_entry_could_not_be_removed_is_said_too(self):
        from trellis.core_actions import Status, status_of
        from trellis.domain_focus_service import TaskService
        from trellis.domain_focus_tool import _erased_message
        from zoneinfo import ZoneInfo

        class Repo:
            def delete(self, user_id, task_id): return True

        class IndexDown:
            def forget(self, kind, entity_id): return False

        result = TaskService(Repo(), ZoneInfo("UTC"), memory=IndexDown()).erase(uuid4(), uuid4())
        assert result.uncertain == ("the search index",)
        assert status_of(_erased_message(result)) is Status.PARTIAL
