"""
Offline proof for the semantic router (houses + rooms).

Two layers:
  1. Deterministic FAKE-embedder tests — prove the ROUTING LOGIC (best house,
     max-over-rooms, ambiguous -> both, weak -> empty, embedder-down -> fallback)
     with no network.
  2. A LIVE run against the real LOCAL embedder (fastembed/bge-small) that prints
     the real per-house score table (best room + score) and asserts the CLEAR
     cases route to the right house and generic chat routes EMPTY (big brain).
     Borderline cases are printed (room-tuning candidates), not failed. The
     printed table is what we tune rooms/thresholds from.

Run:  uv run pytest tests/test_semantic_router.py -q -s
"""
from __future__ import annotations

import unittest

from trellis.infra_router import SemanticRouter
from trellis.domain_focus_tool import FOCUS_ROOMS
from trellis.domain_sense_tool import SENSE_ROOMS
from trellis.domain_move_tool import MOVE_ROOMS

# The real houses — used by the Layer 2 live proof against the actual embedder.
HOUSES = {
    "focus": FOCUS_ROOMS,
    "sense": SENSE_ROOMS,
    "move": MOVE_ROOMS,
}

# Layer 1 proves the ROUTING LOGIC with hand-controlled vectors. Two synthetic
# houses are enough to drive every branch.
_LOGIC_HOUSES = {
    "focus": ["focus room"],
    "move": ["move room"],
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
    return SemanticRouter(_LOGIC_HOUSES, FakeEmbedder(mapping), **kw)


class TestRoutingLogic(unittest.TestCase):
    # Room vectors: move's room -> x-axis, focus's room -> y-axis.
    BASE = {"move room": [1.0, 0.0, 0.0], "focus room": [0.0, 1.0, 0.0]}

    def _map(self, msg, vec):
        return {**self.BASE, msg: vec}

    def test_clear_move_routes_move_only(self):
        r = _router(self._map("m", [1.0, 0.0, 0.0]))
        self.assertEqual(r.route("m"), {"move"})

    def test_clear_focus_routes_focus_only(self):
        r = _router(self._map("m", [0.0, 1.0, 0.0]))
        self.assertEqual(r.route("m"), {"focus"})

    def test_ambiguous_loads_both(self):
        # Equidistant from both -> within close margin -> both houses.
        r = _router(self._map("m", [0.7, 0.7, 0.0]))
        self.assertEqual(r.route("m"), {"move", "focus"})

    def test_weak_match_routes_empty(self):
        # Orthogonal to both room vectors -> top score 0 < min_score -> empty.
        r = _router(self._map("m", [0.0, 0.0, 1.0]))
        self.assertEqual(r.route("m"), set())

    def test_clearly_ahead_not_ambiguous(self):
        # Strong move, weak-but-nonzero focus beyond the close margin.
        r = _router(self._map("m", [1.0, 0.1, 0.0]))
        self.assertEqual(r.route("m"), {"move"})

    def test_house_scores_by_best_room(self):
        # A house with two rooms scores by its BEST-matching room — the sharp
        # match must not be diluted by the house's other rooms.
        houses = {"focus": ["focus room", "shopping room"], "move": ["move room"]}
        mapping = {
            "move room": [1.0, 0.0, 0.0],
            "focus room": [0.0, 1.0, 0.0],
            "shopping room": [0.0, 0.0, 1.0],
            "m": [0.1, 0.0, 0.99],  # almost exactly the shopping room
        }
        r = SemanticRouter(houses, FakeEmbedder(mapping))
        self.assertEqual(r.route("m"), {"focus"})
        score, room = r.explain("m")["focus"]
        self.assertEqual(room, "shopping room")
        self.assertGreater(score, 0.9)

    def test_embedder_down_uses_fallback(self):
        r = SemanticRouter(_LOGIC_HOUSES, BrokenEmbedder(), fallback=_KeywordFallback())
        self.assertEqual(r.route("anything"), {"FELL_BACK"})

    def test_embedder_down_no_fallback_loads_all(self):
        r = SemanticRouter(_LOGIC_HOUSES, BrokenEmbedder())
        self.assertEqual(r.route("anything"), {"focus", "move"})

    def test_room_vectors_embedded_once(self):
        calls = {"n": 0}

        class CountingEmbedder(FakeEmbedder):
            def embed(self_inner, texts):
                calls["n"] += 1
                return super().embed(texts)

        r = SemanticRouter(_LOGIC_HOUSES, CountingEmbedder(self.BASE))
        r.route("a")
        r.route("b")
        # 1 batched call to embed all rooms + 1 per message = 3, not re-embedding
        # rooms each time.
        self.assertEqual(calls["n"], 3)


# --- Wiring: the assembler routes semantically when an embedder is given ----

class TestAssemblerWiring(unittest.TestCase):
    """Stage 4 wiring — Assembler builds a SemanticRouter over the registry's
    houses (each domain's rooms) when an embedder is present, keeps the keyword
    Router (with default domain) when it isn't, and falls back to keywords if
    the embedder dies. Construction-only: oracle/history are never touched here."""

    BASE = {"move room": [1.0, 0.0, 0.0], "focus room": [0.0, 1.0, 0.0]}

    def _registry(self):
        from trellis.core_registry import TrellisRegistry
        registry = TrellisRegistry()
        loader = lambda uid, now: None
        registry.add_domain("focus", loader, [], ["task"], rooms=["focus room"])
        registry.add_domain("move", loader, [], ["run"], rooms=["move room"])
        return registry

    def _assembler(self, embedder):
        from trellis.core_assembler import Assembler
        return Assembler(
            oracle=None,
            registry=self._registry(),
            history=None,
            permanent=[],
            always_tools=[],
            default_domain="focus",
            embedder=embedder,
        )

    def test_registry_returns_rooms(self):
        self.assertEqual(
            self._registry().all_rooms(),
            {"focus": ["focus room"], "move": ["move room"]},
        )

    def test_with_embedder_routes_by_meaning(self):
        a = self._assembler(FakeEmbedder({**self.BASE, "m": [1.0, 0.0, 0.0]}))
        self.assertEqual(a._router.route("m"), {"move"})

    def test_with_embedder_weak_match_is_big_brain_not_default(self):
        # No house lights up -> empty (the big brain carries the turn); the
        # keyword default is NOT injected on a deliberate semantic miss.
        a = self._assembler(FakeEmbedder({**self.BASE, "m": [0.0, 0.0, 1.0]}))
        self.assertEqual(a._router.route("m"), set())

    def test_without_embedder_keeps_keyword_router_with_default(self):
        a = self._assembler(None)
        self.assertEqual(a._router.route("run today"), {"move"})
        self.assertEqual(a._router.route("hello there"), {"focus"})

    def test_embedder_down_falls_back_to_keywords(self):
        a = self._assembler(BrokenEmbedder())
        self.assertEqual(a._router.route("run today"), {"move"})
        self.assertEqual(a._router.route("hello there"), {"focus"})


# --- Layer 2: live proof against the real (local) embedder -----------------

# message -> expected ROUTE result. "move"/"focus"/"sense" = that house must be
# routed. "BIGBRAIN" = must route EMPTY (generic chat — no house lights up; the
# always-on core carries the turn). "BORDERLINE" = genuinely ambiguous with the
# current rooms (informational only — printed, not asserted; these are the
# room-tuning candidates surfaced by the real score table).
LIVE_CASES = [
    # clear move — distinctive running language
    ("what pace for my 6x400m intervals tomorrow?", "move"),
    ("push my long run to sunday", "move"),
    ("how did my run go yesterday?", "move"),
    ("my hips are so tight, give me a mobility routine", "move"),
    ("what's a good warm up before my run?", "move"),
    # clear focus — organising / capture / retrieval
    ("add milk to my shopping list", "focus"),
    ("remind me to call the dentist", "focus"),
    ("idea: a podcast about design", "focus"),
    ("what's on my task list today?", "focus"),
    # clear sense — wellbeing tracking + readiness
    ("log my period started today", "sense"),
    ("I'm exhausted and flat today", "sense"),
    ("how's my readiness today?", "sense"),
    ("took my meds", "sense"),
    # generic chat — no house; the big brain (always-on core) carries the turn
    ("hey", "BIGBRAIN"),
    ("thanks, that's great", "BIGBRAIN"),
    ("morning!", "BIGBRAIN"),
    # borderline — sits on a line between houses with the current rooms.
    ("did I sleep ok for a run tomorrow?", "BORDERLINE"),
    ("what do we do this week?", "BORDERLINE"),
    ("sort out my plan", "BORDERLINE"),
]


class TestLiveRouting(unittest.TestCase):
    def test_real_scores_and_ordering(self):
        try:
            from trellis.infra_embeddings import LocalEmbedder
            embedder = LocalEmbedder()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"local embedder unavailable: {exc}")

        router = SemanticRouter(HOUSES, embedder)
        # Warm the room vectors; bail out gracefully if the model can't load.
        if router.scores("warmup") is None:
            self.skipTest("local embedder unavailable — skipping live check")

        print("\n\n=== SEMANTIC ROUTER — real score table (local bge-small, best room per house) ===")
        print(f"{'message':<38} {'move':>7} {'focus':>7} {'sense':>7}  -> routed [winning room]")
        mismatches = []
        for msg, expected in LIVE_CASES:
            detail = router.explain(msg)
            if detail is None:
                self.skipTest("embedder went unavailable mid-run")
            routed = router.route(msg)
            top_house = max(detail, key=lambda h: detail[h][0])
            top_room = detail[top_house][1]
            t = detail.get("move", (0.0, ""))[0]
            f = detail.get("focus", (0.0, ""))[0]
            s = detail.get("sense", (0.0, ""))[0]
            tag = "  (borderline)" if expected == "BORDERLINE" else ""
            print(
                f"{msg[:37]:<38} {t:>7.3f} {f:>7.3f} {s:>7.3f}  "
                f"-> {sorted(routed) or '[]'} ['{top_room}']{tag}"
            )
            # Assert the CLEAR cases: the expected house must be in the route,
            # and generic chat must route empty (big brain, no house).
            wrong = (
                expected in ("move", "focus", "sense") and expected not in routed
            ) or (expected == "BIGBRAIN" and routed)
            if wrong:
                mismatches.append(
                    (msg, expected, sorted(routed), round(t, 3), round(f, 3), round(s, 3))
                )
        print("=== borderline rows are room-tuning candidates, not failures ===\n")
        self.assertEqual(
            mismatches, [],
            f"clear cases routed wrong: {mismatches}",
        )


if __name__ == "__main__":
    unittest.main()
