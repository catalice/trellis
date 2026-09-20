"""The vault holds things the database can't rebuild: whatever was written in it
by hand. No projection may delete or overwrite that."""
from __future__ import annotations

from datetime import timezone
from uuid import uuid4

from trellis.domain_focus_models import Effort, EffortIntensity
from trellis.infra_obsidian import ObsidianVault


def _vault(tmp_path):
    return ObsidianVault(tmp_path, timezone.utc, None, None, None)


def _effort(title, path):
    return Effort(id=uuid4(), user_id=uuid4(), title=title,
                  intensity=EffortIntensity.ACTIVE, notes=None, obsidian_path=path)


class TestEffortPageRemoval:
    def test_an_untouched_generated_page_is_removed(self, tmp_path):
        vault, effort = _vault(tmp_path), _effort("Garden", "Efforts/Garden.md")
        vault.effort_created(effort)
        assert vault.effort_page_removed(effort.obsidian_path) == "removed"
        assert not (tmp_path / "Efforts/Garden.md").exists()

    def test_a_page_with_hand_written_content_is_kept(self, tmp_path):
        vault, effort = _vault(tmp_path), _effort("Garden", "Efforts/Garden.md")
        vault.effort_created(effort)
        page = tmp_path / "Efforts/Garden.md"
        page.write_text(page.read_text() + "\nPlant the quince before the frost.\n")
        assert vault.effort_page_removed(effort.obsidian_path) == "kept"
        assert "quince" in page.read_text()

    def test_a_missing_page_is_reported_as_missing(self, tmp_path):
        assert _vault(tmp_path).effort_page_removed("Efforts/Nope.md") == "missing"


class TestEffortPageMove:
    def test_rename_onto_an_existing_page_overwrites_nothing(self, tmp_path):
        vault = _vault(tmp_path)
        a = _effort("Alpha", "Efforts/Alpha.md")
        vault.effort_created(a)
        other = tmp_path / "Efforts/Beta.md"
        other.write_text("# Beta\n\nA note written by hand.\n")
        moved = vault.effort_page_moved("Efforts/Alpha.md", _effort("Beta", "Efforts/Beta.md"))
        assert moved == "collision"
        assert "written by hand" in other.read_text()
        assert (tmp_path / "Efforts/Alpha.md").exists()      # the old page stays too

    def test_a_clean_rename_moves_the_page(self, tmp_path):
        vault = _vault(tmp_path)
        vault.effort_created(_effort("Alpha", "Efforts/Alpha.md"))
        assert vault.effort_page_moved("Efforts/Alpha.md", _effort("Gamma", "Efforts/Gamma.md")) == "moved"
        assert (tmp_path / "Efforts/Gamma.md").read_text().startswith("# Gamma")
        assert not (tmp_path / "Efforts/Alpha.md").exists()

    def test_page_exists_lets_a_rename_be_refused_before_anything_changes(self, tmp_path):
        vault = _vault(tmp_path)
        (tmp_path / "Efforts").mkdir()
        (tmp_path / "Efforts/Beta.md").write_text("mine")
        assert vault.page_exists("Efforts/Beta.md") and not vault.page_exists("Efforts/Alpha.md")


class TestLearnMapCollisions:
    def test_titles_that_sanitise_alike_get_separate_pages(self, tmp_path):
        vault = _vault(tmp_path)
        one, two = uuid4(), uuid4()
        vault.learn_map("A/B", "first map", thread_id=one)
        vault.learn_map("AB", "second map", thread_id=two)
        vault.learn_map("A/B", "first map, updated", thread_id=one)
        pages = {p.name: p.read_text() for p in (tmp_path / "Atlas/Maps").glob("*.md")}
        assert len(pages) == 2
        assert sum("first map, updated" in body for body in pages.values()) == 1
        assert sum("second map" in body for body in pages.values()) == 1

    def test_a_hand_written_page_with_the_same_name_is_never_overwritten(self, tmp_path):
        vault = _vault(tmp_path)
        mine = tmp_path / "Atlas/Maps/Rome.md"
        mine.parent.mkdir(parents=True)
        mine.write_text("My own notes on Rome.\n")
        vault.learn_map("Rome", "the map", thread_id=uuid4())
        assert mine.read_text() == "My own notes on Rome.\n"
        assert len(list((tmp_path / "Atlas/Maps").glob("Rome*.md"))) == 2


class TestEffortServiceThroughTheRealVault:
    """The same guarantees, entered the way the bot enters them."""

    class _Repo:
        def __init__(self):
            self.efforts = {}
        def save(self, e):
            self.efforts[e.id] = e
            return e
        def get(self, eid):
            return self.efforts.get(eid)
        def get_by_title(self, uid, title):
            return next((e for e in self.efforts.values() if e.title.lower() == title.strip().lower()), None)
        def list_all(self, uid):
            return list(self.efforts.values())
        def delete(self, uid, eid):
            return self.efforts.pop(eid, None) is not None
        def rename(self, uid, eid, title, obsidian_path):
            from dataclasses import replace
            self.efforts[eid] = replace(self.efforts[eid], title=title, obsidian_path=obsidian_path)
            return True

    class _NoCaptures:
        def for_effort(self, uid, eid):
            return []

    def _svc(self, tmp_path):
        from trellis.domain_focus_service import EffortService
        return EffortService(self._Repo(), projection=_vault(tmp_path))

    def test_erasing_an_effort_keeps_a_page_someone_wrote_on(self, tmp_path):
        from datetime import datetime
        svc, uid = self._svc(tmp_path), uuid4()
        effort = svc.find_or_create(uid, "Garden", datetime.now(timezone.utc))
        page = tmp_path / effort.obsidian_path
        page.write_text(page.read_text() + "\nQuince before the frost.\n")
        assert svc.delete_if_empty(uid, effort.id, self._NoCaptures()) == "deleted_page_kept"
        assert "Quince" in page.read_text()

    def test_rename_onto_a_taken_page_changes_nothing(self, tmp_path):
        import pytest
        from datetime import datetime
        from trellis.domain_focus_service import PageTaken
        svc, uid = self._svc(tmp_path), uuid4()
        effort = svc.find_or_create(uid, "Alpha", datetime.now(timezone.utc))
        (tmp_path / "Efforts/Beta.md").write_text("A note written by hand.\n")
        with pytest.raises(PageTaken):
            svc.rename(uid, effort.id, "Beta")
        assert svc.list_all(uid)[0].title == "Alpha"                      # record unchanged
        assert (tmp_path / "Efforts/Beta.md").read_text() == "A note written by hand.\n"

    def test_a_new_effort_never_moves_into_a_page_that_isnt_its_own(self, tmp_path):
        from datetime import datetime
        svc, uid = self._svc(tmp_path), uuid4()
        (tmp_path / "Efforts").mkdir()
        (tmp_path / "Efforts/Garden.md").write_text("Mine.\n")
        effort = svc.find_or_create(uid, "Garden", datetime.now(timezone.utc))
        assert effort.obsidian_path != "Efforts/Garden.md"
        assert (tmp_path / "Efforts/Garden.md").read_text() == "Mine.\n"
        assert (tmp_path / effort.obsidian_path).exists()


class TestMapPagesKeepWhatWasAddedByHand:
    """The ownership marker says who generated a page, not that nobody edited it."""

    def test_a_paragraph_added_in_the_vault_survives_the_next_update(self, tmp_path):
        vault, thread = _vault(tmp_path), uuid4()
        vault.learn_map("Rome", "first body", thread_id=thread)
        page = tmp_path / "Atlas/Maps/Rome.md"
        page.write_text(page.read_text() + "\nMy own thought about the Gracchi.\n")
        vault.learn_map("Rome", "second body", thread_id=thread)
        text = page.read_text()
        assert "second body" in text and "first body" not in text
        assert "My own thought about the Gracchi." in text

    def test_writing_above_the_generated_part_survives_too(self, tmp_path):
        vault, thread = _vault(tmp_path), uuid4()
        vault.learn_map("Rome", "first body", thread_id=thread)
        page = tmp_path / "Atlas/Maps/Rome.md"
        page.write_text("A note to self at the very top.\n\n" + page.read_text())
        vault.learn_map("Rome", "second body", thread_id=thread)
        assert page.read_text().startswith("A note to self at the very top.")
        assert "second body" in page.read_text()

    def test_a_page_from_before_markers_with_additions_loses_nothing(self, tmp_path):
        vault = _vault(tmp_path)
        page = tmp_path / "Atlas/Maps/Rome.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Rome\n\n*Updated Mon 1 January 2026*\n\n## Republic\n- consuls\n\nMy own margin note.\n")
        vault.learn_map("Rome", "*Updated Tue 2 January 2026*\n\n## Republic\n- consuls\n- tribunes", thread_id=uuid4())
        text = page.read_text()
        assert "- tribunes" in text and "My own margin note." in text

    def test_a_purely_generated_page_from_before_markers_converts_cleanly(self, tmp_path):
        vault = _vault(tmp_path)
        page = tmp_path / "Atlas/Maps/Rome.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Rome\n\n*Updated Mon 1 January 2026*\n\n## Republic\n- consuls")
        vault.learn_map("Rome", "*Updated Tue 2 January 2026*\n\n## Republic\n- consuls", thread_id=uuid4())
        assert page.read_text().count("- consuls") == 1


class TestEffortPageEdgeCases:
    def test_a_note_written_as_a_heading_counts_as_writing(self, tmp_path):
        vault, effort = _vault(tmp_path), _effort("Garden", "Efforts/Garden.md")
        vault.effort_created(effort)
        page = tmp_path / "Efforts/Garden.md"
        page.write_text(page.read_text() + "\n# Quince before the frost\n")
        assert vault.effort_page_removed(effort.obsidian_path) == "kept"

    def test_renaming_an_effort_whose_page_is_shared_leaves_the_page_for_the_other(self, tmp_path):
        from datetime import datetime
        from trellis.domain_focus_service import EffortService
        repo = TestEffortServiceThroughTheRealVault._Repo()
        svc, uid = EffortService(repo, projection=_vault(tmp_path)), uuid4()
        first = svc.find_or_create(uid, "Garden", datetime.now(timezone.utc))
        # A second effort that shares the first one's page — how older installs can look.
        from dataclasses import replace
        second = replace(_effort("Garden!", first.obsidian_path), user_id=uid)
        repo.save(second)
        (tmp_path / first.obsidian_path).write_text("# Garden\n\nShared notes.\n")
        svc.rename(uid, first.id, "Orchard")
        assert (tmp_path / first.obsidian_path).read_text() == "# Garden\n\nShared notes.\n"
        assert "Shared notes." in (tmp_path / "Efforts/Orchard.md").read_text()

    def test_erasing_an_effort_whose_page_is_shared_keeps_the_page(self, tmp_path):
        from datetime import datetime
        from dataclasses import replace
        from trellis.domain_focus_service import EffortService
        repo = TestEffortServiceThroughTheRealVault._Repo()
        svc, uid = EffortService(repo, projection=_vault(tmp_path)), uuid4()
        first = svc.find_or_create(uid, "Garden", datetime.now(timezone.utc))
        repo.save(replace(_effort("Garden!", first.obsidian_path), user_id=uid))
        assert svc.delete_if_empty(uid, first.id, TestEffortServiceThroughTheRealVault._NoCaptures()) == "deleted_page_kept"
        assert (tmp_path / first.obsidian_path).exists()


class TestInterruptedWrites:
    """Opening a file for writing truncates it first. A write that dies half-way
    (disk full) must leave the page — and the handwriting on it — as it was."""

    def test_an_interrupted_map_update_leaves_the_page_intact(self, tmp_path, monkeypatch):
        from pathlib import Path
        vault, thread = _vault(tmp_path), uuid4()
        vault.learn_map("Rome", "first body", thread_id=thread)
        page = tmp_path / "Atlas/Maps/Rome.md"
        page.write_text(page.read_text() + "\nMy own thought about the Gracchi.\n")
        before = page.read_text()

        real_write = Path.write_text
        def dies_half_way(self, data, *args, **kwargs):
            real_write(self, data[:40], *args, **kwargs)
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(Path, "write_text", dies_half_way)
        vault.learn_map("Rome", "second body", thread_id=thread)      # must not raise
        monkeypatch.undo()

        assert page.read_text() == before
        assert [p.name for p in page.parent.iterdir()] == ["Rome.md"]   # no debris left behind


class TestMapPagesCanBeReprojected:
    """After an upgrade, every map is re-projected in one go — while an untouched
    page from before markers still matches what Trellis wrote, so it converts
    cleanly instead of being kept as a duplicate at the next conversation."""

    def test_project_all_converts_an_untouched_old_page_cleanly(self, tmp_path):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from trellis.domain_learn_models import LearnThread
        from trellis.domain_learn_service import LearnService, _map_body

        uid = uuid4()
        thread = LearnThread(id=uuid4(), user_id=uid, title="Rome", position="the Republic",
                             created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))

        class Repo:
            def list_threads(self, user_id):
                return [thread]
            def get_thread_by_title(self, user_id, title):
                return thread
            def list_entries(self, user_id, thread_id):
                return []

        tz = ZoneInfo("UTC")
        page = tmp_path / "Atlas/Maps/Rome.md"
        page.parent.mkdir(parents=True)
        page.write_text(f"# Rome\n\n{_map_body(thread, [], tz)}")        # exactly what the old code wrote

        svc = LearnService(Repo(), tz, projection=_vault(tmp_path))
        assert svc.project_all(uid) == 1
        text = page.read_text()
        assert "trellis:map:" in text and "The page as it was before" not in text
        assert text.count("**You are here:**") == 1

    def test_running_the_backfill_script_converts_the_old_map_pages(self, tmp_path, monkeypatch):
        """The upgrade instruction names this script; running it must do what is promised."""
        import importlib.util
        from datetime import datetime
        from pathlib import Path
        from types import SimpleNamespace
        from zoneinfo import ZoneInfo
        from trellis.domain_learn_models import LearnThread
        from trellis.domain_learn_service import _map_body

        uid, tz = uuid4(), ZoneInfo("UTC")
        thread = LearnThread(id=uuid4(), user_id=uid, title="Rome", position="the Republic",
                             created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        page = tmp_path / "Atlas/Maps/Rome.md"
        page.parent.mkdir(parents=True)
        page.write_text(f"# Rome\n\n{_map_body(thread, [], tz)}")

        spec = importlib.util.spec_from_file_location(
            "backfill_vault_script", Path(__file__).parent.parent / "scripts" / "backfill_vault.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)

        class LearnRepo:
            def __init__(self, database): pass
            def list_threads(self, user_id): return [thread]
            def get_thread_by_title(self, user_id, title): return thread
            def list_entries(self, user_id, thread_id): return []

        class States:
            def __init__(self, database): pass
            def list_states_since(self, user_id, *, since): return []
            def list_events_since(self, user_id, *, since): return []

        real_vault = _vault(tmp_path)
        real_vault.tracking_changed = lambda user_id: None
        real_vault.plan_changed = lambda user_id: None
        monkeypatch.setattr(script, "Settings", SimpleNamespace(
            from_env=lambda: SimpleNamespace(database_url="dsn", timezone=tz)))
        monkeypatch.setattr(script, "PostgresDatabase", lambda url: SimpleNamespace(list_users=lambda: [(uid, 1)]))
        monkeypatch.setattr(script, "PostgresStateRepository", States)
        monkeypatch.setattr(script, "PostgresLearnRepository", LearnRepo)
        monkeypatch.setattr(script, "build_vault", lambda database, settings: real_vault)

        assert script.main() == 0
        text = page.read_text()
        assert "trellis:map:" in text and "The page as it was before" not in text
