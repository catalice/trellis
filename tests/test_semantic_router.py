"""
Offline proof for the semantic router.

Two layers:
  1. Deterministic FAKE-embedder tests — prove the ROUTING LOGIC (best room,
     ambiguous -> both, weak -> empty, embedder-down -> fallback) with no network.
  2. A LIVE run against the real embedder (only if GITHUB_TOKEN is available) that
     prints the real per-room cosine score table and checks the fundamental,
     threshold-independent property: a message about a room scores HIGHER on that
     room than on the others. The printed table is what we tune thresholds from.

Run:  uv run pytest tests/test_semantic_router.py -q -s
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

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


# --- Layer 2: live proof against the real embedder (token-gated) -----------

def _github_token() -> str:
    tok = os.getenv("GITHUB_TOKEN", "")
    if tok:
        return tok
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            s = line.strip()
            if s.startswith("GITHUB_TOKEN="):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# message -> the room it should lean toward ("second_brain" means big-brain/default)
LIVE_CASES = [
    ("what do we do this week?", "training"),
    ("6x400m intervals felt awful", "training"),
    ("did I sleep ok for a run tomorrow?", "training"),
    ("push my long run to sunday", "training"),
    ("how's my readiness today?", "training"),
    ("add milk to my shopping list", "second_brain"),
    ("remind me to call the dentist", "second_brain"),
    ("log my period started today", "second_brain"),
    ("I'm exhausted and flat today", "second_brain"),
    ("idea: a podcast about design", "second_brain"),
    ("sort out my plan", "AMBIGUOUS"),
    ("hey", "AMBIGUOUS"),
]


class TestLiveRouting(unittest.TestCase):
    def test_real_scores_and_ordering(self):
        token = _github_token()
        if not token:
            self.skipTest("no GITHUB_TOKEN — skipping live embedding check")
        from trellis.infra_embeddings import GitHubModelsEmbedder

        router = SemanticRouter(DESCRIPTIONS, GitHubModelsEmbedder(token))
        # Warm the room vectors; bail out gracefully if the API is unavailable.
        if router.scores("warmup") is None:
            self.skipTest("embedder unavailable/rate-limited — skipping live check")

        print("\n\n=== SEMANTIC ROUTER — real score table ===")
        print(f"{'message':<38} {'training':>9} {'2nd_brain':>10}  -> routed")
        mismatches = []
        for msg, expected in LIVE_CASES:
            scores = router.scores(msg)
            if scores is None:
                self.skipTest("embedder went unavailable mid-run")
            routed = router.route(msg)
            t, sb = scores.get("training", 0.0), scores.get("second_brain", 0.0)
            print(f"{msg[:37]:<38} {t:>9.3f} {sb:>10.3f}  -> {sorted(routed) or '[]'}")
            if expected in ("training", "second_brain"):
                higher = "training" if t > sb else "second_brain"
                if higher != expected:
                    mismatches.append((msg, expected, higher, t, sb))
        print("=== (thresholds tune from these numbers; ordering is the core signal) ===\n")
        self.assertEqual(
            mismatches, [],
            f"messages scored higher on the wrong room: {mismatches}",
        )


if __name__ == "__main__":
    unittest.main()
