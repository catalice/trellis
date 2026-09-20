"""Stage 4's 'done when': a rename, a delete and a correction each leave the
record, the search index and the vault saying the same thing.

Authority: the DATABASE is the record. The index and Trellis's own vault text are
rebuilt from it. Writing done by hand in the vault is its own authority and is
never touched."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from trellis.infra_obsidian import ObsidianVault

TZ = ZoneInfo("UTC")
UID = uuid4()


class _Index:
    def __init__(self): self.cards = {}
    def remember(self, user_id, kind, entity_id, text): self.cards[(kind, entity_id)] = text
    def forget(self, kind, entity_id):
        self.cards.pop((kind, entity_id), None)
        return True


class _EffortRepo:
    def __init__(self): self.rows = {}
    def save(self, e):
        self.rows[e.id] = e
        return e
    def get(self, eid): return self.rows.get(eid)
    def get_by_title(self, uid, title):
        return next((e for e in self.rows.values() if e.title.lower() == title.strip().lower()), None)
    def list_all(self, uid): return list(self.rows.values())
    def delete(self, uid, eid): return self.rows.pop(eid, None) is not None
    def rename(self, uid, eid, title, path):
        from dataclasses import replace
        self.rows[eid] = replace(self.rows[eid], title=title, obsidian_path=path)
        return True


def test_a_rename_moves_the_record_the_index_and_the_page_together(tmp_path):
    from trellis.domain_focus_service import EffortService
    repo, index = _EffortRepo(), _Index()
    vault = ObsidianVault(tmp_path, TZ, None, None, repo)
    svc = EffortService(repo, projection=vault, memory=index)
    effort = svc.find_or_create(UID, "Garden", datetime.now(timezone.utc))
    (tmp_path / effort.obsidian_path).write_text((tmp_path / effort.obsidian_path).read_text() + "\nHand-written: quince.\n")

    renamed = svc.rename(UID, effort.id, "Orchard")
    assert repo.get(effort.id).title == "Orchard"                               # the record
    assert "Orchard" in index.cards[("effort", effort.id)] and "Garden" not in index.cards[("effort", effort.id)]
    page = tmp_path / renamed.obsidian_path
    assert page.read_text().startswith("# Orchard") and "Hand-written: quince." in page.read_text()
    assert not (tmp_path / "Efforts/Garden.md").exists()                        # no ghost left behind


def test_a_delete_removes_the_record_the_index_entry_and_trellis_own_text(tmp_path):
    from trellis.domain_focus_models import Capture, CaptureType
    from trellis.domain_focus_service import CaptureService
    capture = Capture(id=uuid4(), user_id=UID, raw="a thing I should not have said", capture_type=CaptureType.IDEA,
                      synthesis="Cleaned: a thing.", summary="A thing", effort_id=None,
                      created_at=datetime(2026, 3, 10, 9, 30, tzinfo=timezone.utc))

    class Repo:
        rows = {capture.id: capture}
        def get(self, uid, cid): return self.rows.get(cid)
        def delete(self, uid, cid): return self.rows.pop(cid, None) is not None

    index = _Index()
    index.remember(UID, "capture", capture.id, "a thing")
    vault = ObsidianVault(tmp_path, TZ, None, None, _EffortRepo())
    vault.capture_saved(capture)
    day = tmp_path / "Calendar/Captures/2026-03-10.md"
    day.write_text(day.read_text() + "\nHand-written, same day.\n")

    result = CaptureService(Repo(), projection=vault, memory=index).erase(UID, capture.id)
    assert result.erased and not result.left_in_vault and not result.uncertain
    assert Repo.rows == {} and index.cards == {}
    assert "should not have said" not in day.read_text() and "Hand-written, same day." in day.read_text()


def test_a_correction_leaves_one_entry_on_the_right_day_everywhere(tmp_path):
    """'Actually, that was yesterday': the wrong entry is erased and the account
    is logged again with its real time. Afterwards the log, and both months'
    vault pages, hold it once — on the day it happened."""
    from trellis.domain_sense_service import SenseService
    from trellis.domain_sense_tool import handle_log_state

    class Repo:
        def __init__(self): self.states, self.events = [], []
        def save_state(self, log):
            self.states.append(log)
            return log
        def save_event(self, event):
            self.events.append(event)
            return event
        def list_states_since(self, uid, *, since): return [s for s in self.states if s.felt_at >= since]
        def list_events_since(self, uid, *, since): return [e for e in self.events if e.occurred_at >= since]
        def last_period_start(self, uid): return None
        def entry_day(self, uid, eid): return next((s.felt_at for s in self.states if s.id == eid), None)
        def delete_state(self, uid, eid):
            before = len(self.states)
            self.states = [s for s in self.states if s.id != eid]
            return len(self.states) < before
        def delete_event(self, uid, eid): return False

    repo = Repo()
    vault = ObsidianVault(tmp_path, TZ, None, None, None, state_repo=repo)
    svc = SenseService(repo, TZ, projection=vault)
    now = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)                       # the 1st — logged on the wrong day

    handle_log_state(UID, {"note": "wiped out after the long run", "energy": 1}, now, sense_service=svc, tz=TZ)
    wrong = repo.states[0]
    vault.write_tracking_month(UID, 2026, 3)
    assert "wiped out" in (tmp_path / "Calendar/Tracking/History/2026-03.md").read_text()

    assert svc.erase_entry(UID, wrong.id).erased                                # the correction: erase…
    handle_log_state(UID, {"note": "wiped out after the long run", "energy": 1, "felt_at": "2026-02-28T19:00"},
                     now, sense_service=svc, tz=TZ)                             # …and log it where it belongs

    assert [s.felt_at.date().isoformat() for s in repo.states] == ["2026-02-28"]            # the record
    assert "wiped out" not in (tmp_path / "Calendar/Tracking/History/2026-03.md").read_text()
    assert (tmp_path / "Calendar/Tracking/History/2026-02.md").read_text().count("wiped out") == 1
