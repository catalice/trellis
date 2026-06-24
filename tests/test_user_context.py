from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

from trellis.user_context import (
    AnchorService,
    CurrentContext,
    CurrentContextService,
    TrainingAnchor,
    UserProfile,
    UserProfileService,
)


# ---------------------------------------------------------------------------
# In-memory fakes
# ---------------------------------------------------------------------------

class FakeAnchorRepository:
    def __init__(self):
        self._anchors: list[TrainingAnchor] = []

    def list_active(self, user_id: UUID) -> list[TrainingAnchor]:
        return [a for a in self._anchors if a.user_id == user_id]

    def save(self, anchor: TrainingAnchor) -> TrainingAnchor:
        self._anchors.append(anchor)
        return anchor

    def deactivate(self, anchor_id: UUID) -> None:
        self._anchors = [a for a in self._anchors if a.id != anchor_id]


class FakeUserProfileRepository:
    def __init__(self):
        self._profiles: dict[UUID, UserProfile] = {}

    def get(self, user_id: UUID) -> UserProfile | None:
        return self._profiles.get(user_id)

    def upsert(self, profile: UserProfile) -> UserProfile:
        self._profiles[profile.user_id] = profile
        return profile


class FakeCurrentContextRepository:
    def __init__(self):
        self._contexts: dict[UUID, CurrentContext] = {}

    def get(self, user_id: UUID) -> CurrentContext | None:
        return self._contexts.get(user_id)

    def upsert(self, context: CurrentContext) -> CurrentContext:
        self._contexts[context.user_id] = context
        return context


# ---------------------------------------------------------------------------
# AnchorService
# ---------------------------------------------------------------------------

class TestAnchorService(unittest.TestCase):
    def setUp(self):
        self.repo = FakeAnchorRepository()
        self.service = AnchorService(self.repo)
        self.user_id = uuid4()

    def test_set_creates_anchor(self):
        anchor = self.service.set(self.user_id, day_of_week=0, kind="strength", label="PT")
        self.assertEqual(0, anchor.day_of_week)
        self.assertEqual("PT", anchor.label)
        self.assertEqual(1, len(self.service.list(self.user_id)))

    def test_set_deduplicates_same_day_and_kind(self):
        self.service.set(self.user_id, day_of_week=0, kind="strength", label="Old PT")
        self.service.set(self.user_id, day_of_week=0, kind="strength", label="New PT")
        anchors = self.service.list(self.user_id)
        self.assertEqual(1, len(anchors))
        self.assertEqual("New PT", anchors[0].label)

    def test_set_does_not_deduplicate_different_kind(self):
        self.service.set(self.user_id, day_of_week=0, kind="strength", label="PT")
        self.service.set(self.user_id, day_of_week=0, kind="social_run", label="Club run")
        self.assertEqual(2, len(self.service.list(self.user_id)))

    def test_set_does_not_deduplicate_different_day(self):
        self.service.set(self.user_id, day_of_week=0, kind="strength", label="PT Mon")
        self.service.set(self.user_id, day_of_week=3, kind="strength", label="PT Thu")
        self.assertEqual(2, len(self.service.list(self.user_id)))

    def test_remove_deactivates_anchor(self):
        anchor = self.service.set(self.user_id, day_of_week=2, kind="social_run", label="Wed run")
        self.service.remove(anchor.id)
        self.assertEqual(0, len(self.service.list(self.user_id)))

    def test_summary_for_coach_returns_none_when_empty(self):
        self.assertIsNone(self.service.summary_for_coach(self.user_id))

    def test_summary_for_coach_describes_anchors(self):
        self.service.set(self.user_id, day_of_week=0, kind="strength", label="PT", time_of_day="09:00")
        summary = self.service.summary_for_coach(self.user_id)
        self.assertIn("Mon", summary)
        self.assertIn("PT", summary)
        self.assertIn("09:00", summary)

    def test_hard_constraint_shown_as_soft_when_false(self):
        self.service.set(self.user_id, day_of_week=2, kind="social_run",
                         label="Wed run", is_hard_constraint=False)
        summary = self.service.summary_for_coach(self.user_id)
        self.assertIn("(soft)", summary)

    def test_deduplication_is_per_user(self):
        other_user = uuid4()
        self.service.set(self.user_id, day_of_week=0, kind="strength", label="PT")
        self.service.set(other_user, day_of_week=0, kind="strength", label="Other PT")
        self.assertEqual(1, len(self.service.list(self.user_id)))
        self.assertEqual(1, len(self.service.list(other_user)))


# ---------------------------------------------------------------------------
# UserProfileService
# ---------------------------------------------------------------------------

class TestUserProfileService(unittest.TestCase):
    def setUp(self):
        self.repo = FakeUserProfileRepository()
        self.service = UserProfileService(self.repo)
        self.user_id = uuid4()

    def test_get_returns_none_when_no_profile(self):
        self.assertIsNone(self.service.get(self.user_id))

    def test_update_creates_profile(self):
        profile = self.service.update(self.user_id, physical_notes="Hypermobile")
        self.assertEqual("Hypermobile", profile.physical_notes)
        self.assertIsNone(profile.cognitive_notes)

    def test_update_merges_fields(self):
        self.service.update(self.user_id, physical_notes="Hypermobile")
        profile = self.service.update(self.user_id, cognitive_notes="ADHD")
        self.assertEqual("Hypermobile", profile.physical_notes)
        self.assertEqual("ADHD", profile.cognitive_notes)

    def test_update_overwrites_specified_field(self):
        self.service.update(self.user_id, physical_notes="Old note")
        profile = self.service.update(self.user_id, physical_notes="New note")
        self.assertEqual("New note", profile.physical_notes)

    def test_for_coach_omits_empty_fields(self):
        profile = self.service.update(self.user_id, physical_notes="Hypermobile")
        text = profile.for_coach()
        self.assertIn("Hypermobile", text)
        self.assertNotIn("Cognitive", text)

    def test_is_empty_true_when_no_notes(self):
        profile = self.service.update(self.user_id)
        self.assertTrue(profile.is_empty())

    def test_is_empty_false_when_any_note(self):
        profile = self.service.update(self.user_id, physical_notes="Something")
        self.assertFalse(profile.is_empty())


# ---------------------------------------------------------------------------
# CurrentContextService
# ---------------------------------------------------------------------------

class TestCurrentContextService(unittest.TestCase):
    def setUp(self):
        self.repo = FakeCurrentContextRepository()
        self.service = CurrentContextService(self.repo)
        self.user_id = uuid4()
        self.today = date(2026, 6, 21)

    def test_get_valid_returns_none_when_no_context(self):
        self.assertIsNone(self.service.get_valid(self.user_id, self.today))

    def test_get_valid_returns_none_when_expired(self):
        self.service.update(self.user_id, physical_notes="Back sore",
                            valid_days=1, today=date(2026, 6, 1))
        self.assertIsNone(self.service.get_valid(self.user_id, self.today))

    def test_get_valid_returns_context_when_valid(self):
        self.service.update(self.user_id, physical_notes="Back sore",
                            valid_days=14, today=self.today)
        ctx = self.service.get_valid(self.user_id, self.today)
        self.assertIsNotNone(ctx)
        self.assertEqual("Back sore", ctx.physical_notes)

    def test_update_sets_valid_until(self):
        ctx = self.service.update(self.user_id, valid_days=7, today=self.today)
        self.assertEqual(self.today + timedelta(days=7), ctx.valid_until)

    def test_update_merges_fields(self):
        self.service.update(self.user_id, physical_notes="Back sore",
                            valid_days=14, today=self.today)
        ctx = self.service.update(self.user_id, misc_notes="Travelling",
                                  valid_days=14, today=self.today)
        self.assertEqual("Back sore", ctx.physical_notes)
        self.assertEqual("Travelling", ctx.misc_notes)

    def test_update_overwrites_specified_field(self):
        self.service.update(self.user_id, physical_notes="Old",
                            valid_days=14, today=self.today)
        ctx = self.service.update(self.user_id, physical_notes="New",
                                  valid_days=14, today=self.today)
        self.assertEqual("New", ctx.physical_notes)

    def test_clear_specific_field(self):
        self.service.update(self.user_id, physical_notes="Back sore",
                            misc_notes="Travelling", valid_days=14, today=self.today)
        self.service.clear(self.user_id, ["physical_notes"])
        ctx = self.repo.get(self.user_id)
        self.assertIsNone(ctx.physical_notes)
        self.assertEqual("Travelling", ctx.misc_notes)

    def test_clear_all_fields(self):
        self.service.update(self.user_id, physical_notes="Back sore",
                            cognitive_notes="Tired", misc_notes="Travelling",
                            valid_days=14, today=self.today)
        self.service.clear(self.user_id)
        ctx = self.repo.get(self.user_id)
        self.assertIsNone(ctx.physical_notes)
        self.assertIsNone(ctx.cognitive_notes)
        self.assertIsNone(ctx.misc_notes)

    def test_clear_is_noop_when_no_context(self):
        self.service.clear(self.user_id)  # should not raise

    def test_for_coach_includes_all_set_fields(self):
        ctx = self.service.update(self.user_id, physical_notes="Back sore",
                                  cognitive_notes="Foggy", misc_notes="Travelling",
                                  valid_days=14, today=self.today)
        text = ctx.for_coach()
        self.assertIn("Back sore", text)
        self.assertIn("Foggy", text)
        self.assertIn("Travelling", text)

    def test_for_coach_omits_none_fields(self):
        ctx = self.service.update(self.user_id, misc_notes="Wedding planning",
                                  valid_days=14, today=self.today)
        text = ctx.for_coach()
        self.assertIn("Wedding planning", text)
        self.assertNotIn("Physical", text)
        self.assertNotIn("cognitive", text.lower())


if __name__ == "__main__":
    unittest.main()
