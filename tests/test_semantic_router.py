"""
Offline proof for the semantic router.

Two layers:
  1. Deterministic FAKE-embedder tests — prove the ROUTING LOGIC (best room,
     ambiguous -> both, weak -> empty, embedder-down -> fallback) with no network.
  2. A LIVE run against the real LOCAL embedder (fastembed/bge-small) that prints
     the real per-room cosine score table and asserts the CLEAR cases route to the
     right room. Borderline cases are printed (description-tuning candidates), not
     failed. The printed table is what we tune descriptions/thresholds from.

Run:  uv run pytest tests/test_semantic_router.py -q -s
"""
from __future__ import annotations

import unittest

from trellis.infra_router import SemanticRouter
from trellis.domain_second_brain_tool import SECOND_BRAIN_DESCRIPTION
from trellis.domain_training_tool import TRAINING_DESCRIPTION

DESCRIPTIONS = {
    "second_brain": SECOND_BRAIN_DESCRIPTION,
    "training": TRAINING_DESCRIPTION,
}


# --- Layer 1: deterministic logic tests (no network) -----------------------

class FakeEmbedder:
    """Returns a fixed vector per exact text; unknown text -> an orthogonal
    'nothing' vector. Lets us drive cosine outcomes deterministically."""

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    def embed(self, texts):
        return [self._mapping.get(t, [0.0, 0.0, 1.0]) for t in texts]


class BrokenEmbedder:
    def embed(self, texts):
        return None


class _KeywordFallback:
    def route(self, message: str) -> set[str]:
        return {"FELL_BACK"}


def _router(mapping, **kw) -> SemanticRouter:
    return SemanticRouter(DESCRIPTIONS, FakeEmbedder(mapping), **kw)


class TestRoutingLogic(unittest.TestCase):
    # Room description vectors: training -> x-axis, second_brain -> y-axis.
    BASE = {TRAINING_DESCRIPTION: [1.0, 0.0, 0.0], SECOND_BRAIN_DESCRIPTION: [0.0, 1.0, 0.0]}

    def _map(self, msg, vec):
        return {**self.BASE, msg: vec}

    def test_clear_training_routes_training_only(self):
        r = _router(self._map("m", [1.0, 0.0, 0.0]))
        self.assertEqual(r.route("m"), {"training"})

    def test_clear_second_brain_routes_second_brain_only(self):
        r = _router(self._map("m", [0.0, 1.0, 0.0]))
        self.assertEqual(r.route("m"), {"second_brain"})

    def test_ambiguous_loads_both(self):
        # Equidistant from both -> within close margin -> both rooms.
        r = _router(self._map("m", [0.7, 0.7, 0.0]))
        self.assertEqual(r.route("m"), {"training", "second_brain"})

    def test_weak_match_routes_empty(self):
        # Orthogonal to both room vectors -> top score 0 < min_score -> empty.
        r = _router(self._map("m", [0.0, 0.0, 1.0]))
        self.assertEqual(r.route("m"), set())

    def test_clearly_ahead_not_ambiguous(self):
        # Strong training, weak-but-nonzero second_brain beyond the close margin.
        r = _router(self._map("m", [1.0, 0.1, 0.0]))
        self.assertEqual(r.route("m"), {"training"})

    def test_embedder_down_uses_fallback(self):
        r = SemanticRouter(DESCRIPTIONS, BrokenEmbedder(), fallback=_KeywordFallback())
        self.assertEqual(r.route("anything"), {"FELL_BACK"})

    def test_embedder_down_no_fallback_loads_all(self):
        r = SemanticRouter(DESCRIPTIONS, BrokenEmbedder())
        self.assertEqual(r.route("anything"), {"second_brain", "training"})

    def test_room_vectors_embedded_once(self):
        calls = {"n": 0}

        class CountingEmbedder(FakeEmbedder):
            def embed(self_inner, texts):
                calls["n"] += 1
                return super().embed(texts)

        r = SemanticRouter(DESCRIPTIONS, CountingEmbedder(self.BASE))
        r.route("a")
        r.route("b")
        # 1 call to embed the 2 descriptions + 1 per message = 3, not re-embedding
        # descriptions each time.
        self.assertEqual(calls["n"], 3)


# --- Layer 2: live proof against the real (local) embedder -----------------

# message -> expected room in the ROUTE result. "training"/"second_brain" = that
# room must be routed. "BORDERLINE" = genuinely ambiguous with the current
# descriptions (informational only — printed, not asserted; these are the
# description-tuning candidates surfaced by the real score table).
LIVE_CASES = [
    # clear training — distinctive running language
    ("6x400m intervals felt awful", "training"),
    ("did I sleep ok for a run tomorrow?", "training"),
    ("push my long run to sunday", "training"),
    # clear second_brain / big-brain
    ("add milk to my shopping list", "second_brain"),
    ("remind me to call the dentist", "second_brain"),
    ("log my period started today", "second_brain"),
    ("I'm exhausted and flat today", "second_brain"),
    ("idea: a podcast about design", "second_brain"),
    ("hey", "second_brain"),
    # borderline — the big-brain owns wellbeing/sleep tracking, so generic
    # "readiness / what do we do this week" sit on the line. Tune descriptions.
    ("what do we do this week?", "BORDERLINE"),
    ("how's my readiness today?", "BORDERLINE"),
    ("sort out my plan", "BORDERLINE"),
]


class TestLiveRouting(unittest.TestCase):
    def test_real_scores_and_ordering(self):
        try:
            from trellis.infra_embeddings import LocalEmbedder
            embedder = LocalEmbedder()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"local embedder unavailable: {exc}")

        router = SemanticRouter(DESCRIPTIONS, embedder)
        # Warm the room vectors; bail out gracefully if the model can't load.
        if router.scores("warmup") is None:
            self.skipTest("local embedder unavailable — skipping live check")

        print("\n\n=== SEMANTIC ROUTER — real score table (local bge-small) ===")
        print(f"{'message':<38} {'training':>9} {'2nd_brain':>10}  -> routed")
        mismatches = []
        for msg, expected in LIVE_CASES:
            scores = router.scores(msg)
            if scores is None:
                self.skipTest("embedder went unavailable mid-run")
            routed = router.route(msg)
            t, sb = scores.get("training", 0.0), scores.get("second_brain", 0.0)
            tag = "  (borderline)" if expected == "BORDERLINE" else ""
            print(f"{msg[:37]:<38} {t:>9.3f} {sb:>10.3f}  -> {sorted(routed) or '[]'}{tag}")
            # Assert only the CLEAR cases: the expected room must be in the route.
            if expected in ("training", "second_brain") and expected not in routed:
                mismatches.append((msg, expected, sorted(routed), round(t, 3), round(sb, 3)))
        print("=== borderline rows are description-tuning candidates, not failures ===\n")
        self.assertEqual(
            mismatches, [],
            f"clear cases routed wrong (expected room not loaded): {mismatches}",
        )


if __name__ == "__main__":
    unittest.main()
