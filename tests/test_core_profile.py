from __future__ import annotations

import unittest
from datetime import date, timedelta
from uuid import UUID, uuid4

from datetime import datetime, timezone

from trellis.core_meta_tool import handle_save_preferences, handle_update_current_context
from trellis.core_profile import (
    CONTEXT_CAP,
    AtCap,
    ContextEntry,
    CurrentContextService,
    LineGuard,
    TooLong,
    UserProfile,
    UserProfileService,
)


# ---------------------------------------------------------------------------
# In-memory fakes
# ---------------------------------------------------------------------------

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
        self.entries: list[ContextEntry] = []

    def live(self, user_id: UUID, today: date) -> list[ContextEntry]:
        return sorted(
            (e for e in self.entries if e.user_id == user_id and e.expires_on >= today),
            key=lambda e: e.said_on, reverse=True,
        )

    def add(self, entry: ContextEntry) -> ContextEntry:
        self.entries.append(entry)
        return entry

    def remove(self, user_id: UUID, entry_id: UUID) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.id != entry_id]
        return len(self.entries) < before


class FakeEmbedder:
    """Bag-of-words vectors: shared words = similar. Enough to test the wiring."""
    def embed(self, texts):
        vocab = sorted({w for t in texts for w in t.lower().split()})
        return [[float(t.lower().split().count(w)) for w in vocab] for t in texts]


class FakePreferencesRepository:
    def __init__(self):
        self.rules: list[dict] = []

    def list_rules(self, user_id, domain=None):
        return list(self.rules)

    def add_rule(self, user_id, domain, rule):
        self.rules.append({"id": uuid4(), "domain": domain, "rule": rule})

    def update_rule(self, user_id, rule_id, rule):
        for r in self.rules:
            if r["id"] == rule_id:
                r["rule"] = rule
                return True
        return False


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
# CurrentContextService — a short dated log, shape enforced in Python
# ---------------------------------------------------------------------------

class TestCurrentContextService(unittest.TestCase):
    def setUp(self):
        self.repo = FakeCurrentContextRepository()
        self.service = CurrentContextService(self.repo, LineGuard(FakeEmbedder()))
        self.user_id = uuid4()
        self.today = date(2026, 6, 21)

    def test_entry_is_dated_by_python_and_lapses_after_a_fortnight(self):
        entry, similar = self.service.add(self.user_id, "back sore since the long run", today=self.today)
        self.assertEqual(self.today, entry.said_on)
        self.assertEqual(self.today + timedelta(days=14), entry.expires_on)
        self.assertIsNone(similar)
        self.assertEqual([], self.service.live(self.user_id, self.today + timedelta(days=15)))

    def test_until_overrides_the_fortnight(self):
        entry, _ = self.service.add(self.user_id, "travelling", today=self.today, until=date(2026, 9, 1))
        self.assertEqual(date(2026, 9, 1), entry.expires_on)

    def test_over_the_word_limit_is_refused_not_trimmed(self):
        with self.assertRaises(TooLong) as caught:
            self.service.add(self.user_id, "one two three four five six seven eight nine ten eleven", today=self.today)
        self.assertEqual((11, 10), (caught.exception.words, caught.exception.limit))
        self.assertEqual([], self.repo.entries)

    def test_a_near_repeat_is_saved_and_named(self):
        self.service.add(self.user_id, "back sore since the long run", today=self.today)
        entry, similar = self.service.add(self.user_id, "back sore since the long run yesterday", today=self.today)
        self.assertEqual("back sore since the long run", similar)
        self.assertIn(entry, self.repo.entries)

    def test_full_context_refuses_and_hands_back_what_is_there(self):
        for n in range(CONTEXT_CAP):
            self.service.add(self.user_id, f"thing number {n} zz{n}", today=self.today)
        with self.assertRaises(AtCap) as caught:
            self.service.add(self.user_id, "one more", today=self.today)
        self.assertEqual(CONTEXT_CAP, len(caught.exception.entries))

    def test_remove_by_their_words_needs_exactly_one_match(self):
        self.service.add(self.user_id, "back sore since the long run", today=self.today)
        self.service.add(self.user_id, "back at work on Monday", today=self.today)
        self.assertIsNone(self.service.remove(self.user_id, "back", today=self.today))
        gone = self.service.remove(self.user_id, "sore", today=self.today)
        self.assertEqual("back sore since the long run", gone.text)
        self.assertEqual(1, len(self.service.live(self.user_id, self.today)))


class TestContextAndPreferenceHandlers(unittest.TestCase):
    NOW = datetime(2026, 6, 21, 10, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.guard = LineGuard(FakeEmbedder(), reference=["Never invent data"])
        self.service = CurrentContextService(FakeCurrentContextRepository(), self.guard)
        self.prefs = FakePreferencesRepository()
        self.user_id = uuid4()

    def test_context_refusal_tells_the_model_the_limit(self):
        out = handle_update_current_context(
            self.user_id, {"text": "a b c d e f g h i j k l"}, self.NOW, context_service=self.service)
        self.assertIn("12 words, the limit is 10", out)

    def test_context_bad_until_saves_nothing(self):
        out = handle_update_current_context(
            self.user_id, {"text": "travelling", "until": "next week"}, self.NOW, context_service=self.service)
        self.assertIn("YYYY-MM-DD", out)
        self.assertEqual([], self.service.live(self.user_id, self.NOW.date()))

    def test_long_preference_is_refused_on_add_and_update(self):
        long_rule = "please always make sure that you never ever do this one thing"
        out = handle_save_preferences(self.user_id, {"action": "add", "text": long_rule}, self.NOW,
                                      preferences_repository=self.prefs, guard=self.guard)
        self.assertIn("Not saved", out)
        self.assertEqual([], self.prefs.rules)
        self.prefs.add_rule(self.user_id, "global", "Short rule")
        out = handle_save_preferences(
            self.user_id, {"action": "update", "rule_id": str(self.prefs.rules[0]["id"]), "text": long_rule},
            self.NOW, preferences_repository=self.prefs, guard=self.guard)
        self.assertIn("Not saved", out)
        self.assertEqual("Short rule", self.prefs.rules[0]["rule"])

    def test_preference_repeating_the_constitution_is_saved_and_named(self):
        out = handle_save_preferences(self.user_id, {"action": "add", "text": "Never invent data please"},
                                      self.NOW, preferences_repository=self.prefs, guard=self.guard)
        self.assertIn("Rule saved", out)
        self.assertIn("Never invent data", out)
        self.assertEqual(1, len(self.prefs.rules))


if __name__ == "__main__":
    unittest.main()
