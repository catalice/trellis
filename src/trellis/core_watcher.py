"""
The Watcher — the big brain's slow mind (see docs/DIRECTION.md, "The Two Minds").

Watches everything Trellis records — across houses, across time — for patterns
no single day can show. Two-phase by design:

  DISCOVERY (Claude, ~weekly, one background call — the per-TURN one-call rule
  is untouched): reads a compact summary of the garden and proposes hypotheses.
  Nothing is ever planted in code — discovery is the ONLY source of hypotheses
  (her rule: no enumeration; examples become walls).

  VERIFICATION (Python, deterministic): every testable hypothesis is checked
  against the actual data. Only what verifies is ever offered. A hypothesis
  discovery can't express as a computable test stays visibly 'proposed' —
  honest about its own limits.

Her verdict is ADOPTION, not fact-checking: verification proves the numbers,
she rules on the meaning. adopted = may quietly shape decisions; dismissed =
never resurfaces; watching = keep testing. It learns about her; it never
manages her.

Surfacing: verified patterns ride the assembler's intelligence slot into the
oracle's context — offered gently in conversation, in Trellis's one voice,
when a moment fits. The vault's Watcher page shows EVERYTHING it is thinking,
any time she wants to look.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from psycopg2.extras import Json, RealDictCursor

_log = logging.getLogger(__name__)

# --- Evidence thresholds (facts are Python's) ------------------------------
# Silent below evidence: thin-data confidence is worse than nothing.
_MIN_PAIRS_CORRELATION = 10
_MIN_SAMPLES_PER_SIDE = 6
_MIN_CORRELATION_R = 0.5
# Minimum mean difference per metric for a condition_compare to count as real.
_EFFECT_THRESHOLDS = {
    "energy": 0.7, "mood": 0.7,                      # 1-5 scales
    "sleep_hours": 0.8,
    "sleep_score": 8.0, "hrv": 6.0, "body_battery": 10.0,
    "resting_hr": 3.0, "stress": 8.0,
}
_FRAME_DAYS = 120          # how far back the daily frame reaches
_DISCOVERY_EVERY_DAYS = 7  # discovery cadence (verification is cheap, runs each tick)
_MAX_NEW_HYPOTHESES = 3


# --- Repository ------------------------------------------------------------

class PostgresWatcherRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    def add(self, user_id: UUID, hypothesis: str, test_spec: dict | None) -> None:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO watcher_patterns (id, user_id, hypothesis, test_spec)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (uuid4(), user_id, hypothesis,
                     Json(test_spec) if test_spec is not None else None),
                )

    def all_for(self, user_id: UUID) -> list[dict]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM watcher_patterns WHERE user_id = %s"
                    " ORDER BY proposed_at DESC",
                    (user_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    def set_verification(self, pattern_id: UUID, *, verified: bool,
                         evidence: str, stats: dict) -> None:
        """Record a verification run. Verifying promotes proposed/watching ->
        verified; failing to verify never demotes an adopted pattern (her
        verdict outranks a noisy week) — it just refreshes the evidence."""
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                if verified:
                    cur.execute(
                        """
                        UPDATE watcher_patterns
                        SET status = CASE WHEN status IN ('proposed', 'watching')
                                          THEN 'verified' ELSE status END,
                            evidence = %s, stats = %s, verified_at = NOW()
                        WHERE id = %s
                        """,
                        (evidence, Json(stats), pattern_id),
                    )
                else:
                    cur.execute(
                        "UPDATE watcher_patterns SET evidence = %s, stats = %s"
                        " WHERE id = %s",
                        (evidence, Json(stats), pattern_id),
                    )

    def resolve(self, user_id: UUID, pattern_id: UUID, status: str,
                note: str | None) -> dict | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    UPDATE watcher_patterns
                    SET status = %s, resolved_at = NOW(), resolution_note = %s
                    WHERE id = %s AND user_id = %s
                    RETURNING *
                    """,
                    (status, note, pattern_id, user_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def last_discovery_at(self, user_id: UUID) -> datetime | None:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(proposed_at) FROM watcher_patterns WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
        return row[0] if row else None


# --- The daily frame (facts are Python's) ----------------------------------

class _StateRepo(Protocol):
    def list_states_since(self, user_id: UUID, *, since: datetime) -> list: ...
    def list_events_since(self, user_id: UUID, *, since: datetime) -> list: ...


class _HealthRepo(Protocol):
    def daily_health_since(self, user_id: UUID, *, since: date) -> list: ...


class _RunRepo(Protocol):
    def recent_runs(self, user_id: UUID, *, limit: int) -> list: ...


def build_daily_frame(user_id: UUID, *, states, events, health_rows, runs,
                      tz, today: date) -> dict[date, dict[str, Any]]:
    """One row per day with everything the verifier can test against. Pure
    function of the data — deterministic, unit-testable."""
    frame: dict[date, dict[str, Any]] = {}

    def row(d: date) -> dict[str, Any]:
        return frame.setdefault(d, {})

    by_day_scores: dict[date, dict[str, list]] = {}
    for s in states:
        d = s.felt_at.astimezone(tz).date()
        bucket = by_day_scores.setdefault(d, {"energy": [], "mood": []})
        if s.energy:
            bucket["energy"].append(s.energy)
        if s.mood:
            bucket["mood"].append(s.mood)
    for d, bucket in by_day_scores.items():
        if bucket["energy"]:
            row(d)["energy"] = sum(bucket["energy"]) / len(bucket["energy"])
        if bucket["mood"]:
            row(d)["mood"] = sum(bucket["mood"]) / len(bucket["mood"])

    period_starts: list[date] = []
    for e in events:
        d = e.occurred_at.astimezone(tz).date()
        etype = str(e.event_type)
        if etype == "sleep" and e.value is not None:
            row(d)["sleep_hours"] = float(e.value)
        elif etype == "meds":
            row(d)["meds"] = True
        elif etype == "period_start":
            period_starts.append(d)
    period_starts.sort()

    for h in health_rows:
        d = h.observed_on
        if h.sleep_score is not None:
            row(d)["sleep_score"] = h.sleep_score
        if h.sleep_duration_minutes is not None and "sleep_hours" not in row(d):
            row(d)["sleep_hours"] = round(h.sleep_duration_minutes / 60, 1)
        if h.hrv_last_night is not None:
            row(d)["hrv"] = float(h.hrv_last_night)
        bb = h.body_battery_end or h.body_battery_maximum
        if bb is not None:
            row(d)["body_battery"] = bb
        if h.resting_heart_rate is not None:
            row(d)["resting_hr"] = h.resting_heart_rate
        if h.average_stress is not None:
            row(d)["stress"] = h.average_stress

    for r in runs:
        row(r.ran_on)["ran"] = True

    # Cycle phase per day, from the most recent period start on or before it.
    for d in list(frame.keys()):
        prior = [p for p in period_starts if p <= d]
        if prior:
            cycle_day = (d - prior[-1]).days + 1
            if cycle_day <= 60:
                row(d)["cycle_day"] = cycle_day
                if cycle_day <= 5:
                    row(d)["phase"] = "menstruation"
                elif cycle_day <= 13:
                    row(d)["phase"] = "follicular"
                elif cycle_day <= 16:
                    row(d)["phase"] = "ovulation"
                else:
                    row(d)["phase"] = "luteal"
    return frame


# --- Verification (deterministic) ------------------------------------------

def _condition_holds(day_row: dict, prev_row: dict | None, condition: str) -> bool | None:
    """None = condition can't be evaluated for this day (missing data)."""
    if condition.startswith("phase:"):
        phase = day_row.get("phase")
        if phase is None:
            return None
        return phase == condition.split(":", 1)[1]
    if condition.startswith("dow:"):
        return None  # weekday is derived by the caller — see verify()
    if condition == "ran_today":
        return bool(day_row.get("ran"))
    if condition == "ran_yesterday":
        return bool(prev_row.get("ran")) if prev_row is not None else False
    if condition == "meds_logged":
        return bool(day_row.get("meds"))
    return None


def verify(frame: dict[date, dict], test_spec: dict) -> tuple[bool, str, dict]:
    """Run one test spec against the frame. Returns (verified, evidence, stats).
    Unknown test types / conditions verify nothing — they report themselves."""
    ttype = str(test_spec.get("type", ""))
    days = sorted(frame.keys())

    if ttype == "correlation":
        a_key = str(test_spec.get("series_a", ""))
        b_key = str(test_spec.get("series_b", ""))
        lag = int(test_spec.get("lag_days", 0) or 0)
        pairs = []
        for d in days:
            b_day = d + timedelta(days=lag)
            a_val = frame.get(d, {}).get(a_key)
            b_val = frame.get(b_day, {}).get(b_key)
            if a_val is not None and b_val is not None:
                pairs.append((float(a_val), float(b_val)))
        n = len(pairs)
        if n < _MIN_PAIRS_CORRELATION:
            return False, f"only {n} usable day-pairs (need {_MIN_PAIRS_CORRELATION}) — keep gathering", {"n": n}
        r = _pearson(pairs)
        stats = {"n": n, "r": round(r, 3), "lag_days": lag}
        direction = "rises with" if r > 0 else "falls as"
        evidence = f"{a_key} {direction} {b_key}{f' {lag}d later' if lag else ''}: r={r:.2f} over {n} days"
        return abs(r) >= _MIN_CORRELATION_R, evidence, stats

    if ttype == "condition_compare":
        metric = str(test_spec.get("metric", ""))
        condition = str(test_spec.get("condition", ""))
        with_c, without_c = [], []
        for i, d in enumerate(days):
            val = frame[d].get(metric)
            if val is None:
                continue
            if condition.startswith("dow:"):
                holds = d.weekday() == int(condition.split(":", 1)[1])
            else:
                prev = frame.get(d - timedelta(days=1))
                holds = _condition_holds(frame[d], prev, condition)
            if holds is None:
                continue
            (with_c if holds else without_c).append(float(val))
        n1, n2 = len(with_c), len(without_c)
        if n1 < _MIN_SAMPLES_PER_SIDE or n2 < _MIN_SAMPLES_PER_SIDE:
            return False, f"{n1} days with / {n2} without (need {_MIN_SAMPLES_PER_SIDE} each) — keep gathering", {"n_with": n1, "n_without": n2}
        m1 = sum(with_c) / n1
        m2 = sum(without_c) / n2
        diff = m1 - m2
        threshold = _EFFECT_THRESHOLDS.get(metric, 0.7)
        stats = {"n_with": n1, "n_without": n2,
                 "mean_with": round(m1, 2), "mean_without": round(m2, 2),
                 "diff": round(diff, 2), "threshold": threshold}
        evidence = (f"{metric} averages {m1:.1f} when {condition} vs {m2:.1f} otherwise "
                    f"({n1} vs {n2} days)")
        return abs(diff) >= threshold, evidence, stats

    return False, f"unknown test type {ttype!r} — can't verify this yet", {"error": "unknown_test"}


def _pearson(pairs: list[tuple[float, float]]) -> float:
    n = len(pairs)
    ax = sum(p[0] for p in pairs) / n
    bx = sum(p[1] for p in pairs) / n
    cov = sum((a - ax) * (b - bx) for a, b in pairs)
    var_a = math.sqrt(sum((a - ax) ** 2 for a, _ in pairs))
    var_b = math.sqrt(sum((b - bx) ** 2 for _, b in pairs))
    if var_a == 0 or var_b == 0:
        return 0.0
    return cov / (var_a * var_b)


# --- Discovery (Claude — the only source of hypotheses) --------------------

_DISCOVERY_SYSTEM = """\
You are the Watcher — the slow mind of a personal second brain. You are shown a
compact summary of one person's recorded life (wellbeing states, sleep, meds,
menstrual cycle, runs and how they felt, health metrics) plus the hypotheses
you are already tracking.

Propose AT MOST {max_new} NEW pattern hypotheses worth watching — things the
data itself hints at. Cross-domain patterns (sleep -> run quality, cycle ->
energy, meds timing -> afternoon mood) are your specialty. Fewer is better;
ZERO is a fine answer. Never duplicate or rephrase an existing hypothesis.

Return ONLY valid JSON:
{{"hypotheses": [{{"hypothesis": "<one plain sentence, their data's words>",
  "test": <test-spec or null>}}]}}

A test-spec makes your hypothesis checkable by deterministic code. Vocabulary
(these are the VERBS you may use — the content is yours):
- {{"type": "correlation", "series_a": "<metric>", "series_b": "<metric>",
   "lag_days": <int, 0-3>}} — does one series move with another?
- {{"type": "condition_compare", "metric": "<metric>", "condition": "<cond>"}}
   — is a metric different under a condition?
Metrics: energy, mood, sleep_hours, sleep_score, hrv, body_battery,
resting_hr, stress. Conditions: phase:menstruation, phase:follicular,
phase:ovulation, phase:luteal, ran_today, ran_yesterday, meds_logged,
dow:0..6 (Monday=0).
Use null for a hypothesis you cannot express in that vocabulary — it will be
shown honestly as not-yet-testable rather than silently dropped.\
"""


class WatcherDiscovery:
    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    def propose(self, garden_summary: str, existing: list[str]) -> list[tuple[str, dict | None]]:
        existing_text = "\n".join(f"- {h}" for h in existing) or "(none yet)"
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=16000,
                system=_DISCOVERY_SYSTEM.format(max_new=_MAX_NEW_HYPOTHESES),
                messages=[{
                    "role": "user",
                    "content": f"THE GARDEN:\n{garden_summary}\n\n"
                               f"HYPOTHESES ALREADY TRACKED:\n{existing_text}",
                }],
            )
            raw = "".join(b.text for b in response.content
                          if getattr(b, "type", None) == "text").strip()
            return _parse_hypotheses(raw)
        except Exception:
            _log.warning("watcher discovery failed", exc_info=True)
            return []


def _parse_hypotheses(raw: str) -> list[tuple[str, dict | None]]:
    text = raw.strip()
    if text.startswith("```"):
        first = text.find("\n")
        if first != -1:
            text = text[first + 1:]
        if text.endswith("```"):
            text = text[:-3]
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        _log.warning("watcher: discovery response was not valid JSON")
        return []
    out: list[tuple[str, dict | None]] = []
    for h in data.get("hypotheses", [])[:_MAX_NEW_HYPOTHESES]:
        if not isinstance(h, dict):
            continue
        hypothesis = str(h.get("hypothesis", "")).strip()
        if not hypothesis:
            continue
        test = h.get("test")
        out.append((hypothesis, test if isinstance(test, dict) else None))
    return out


# --- The Watcher itself -----------------------------------------------------

class Watcher:
    def __init__(
        self,
        repo: PostgresWatcherRepository,
        discovery: WatcherDiscovery,
        *,
        state_repo: _StateRepo,
        health_repo: _HealthRepo,
        run_repo: _RunRepo,
        tz,
        vault=None,   # projection with .watcher_page(body); best-effort
    ) -> None:
        self._repo = repo
        self._discovery = discovery
        self._states = state_repo
        self._health = health_repo
        self._runs = run_repo
        self._tz = tz
        self._vault = vault

    # -- the periodic tick (called from the background loop, ~daily) ---------

    def tick(self, user_id: UUID, now: datetime) -> None:
        """Verify everything testable (cheap, deterministic), then let discovery
        propose if it's been quiet for a week. Never raises."""
        try:
            frame = self._frame(user_id, now)
            self._verify_all(user_id, frame)
            last = self._repo.last_discovery_at(user_id)
            if last is None or (now - last) >= timedelta(days=_DISCOVERY_EVERY_DAYS):
                self._discover(user_id, frame)
            self._project(user_id)
        except Exception:
            _log.warning("watcher tick failed", exc_info=True)

    def _frame(self, user_id: UUID, now: datetime) -> dict[date, dict]:
        since_dt = now - timedelta(days=_FRAME_DAYS)
        today = now.astimezone(self._tz).date()
        return build_daily_frame(
            user_id,
            states=self._states.list_states_since(user_id, since=since_dt),
            events=self._states.list_events_since(user_id, since=since_dt),
            health_rows=self._health.daily_health_since(user_id, since=today - timedelta(days=_FRAME_DAYS)),
            runs=self._runs.recent_runs(user_id, limit=400),
            tz=self._tz,
            today=today,
        )

    def _verify_all(self, user_id: UUID, frame: dict) -> None:
        for p in self._repo.all_for(user_id):
            if p["status"] not in ("proposed", "watching", "verified", "adopted"):
                continue
            if not p["test_spec"]:
                continue
            verified, evidence, stats = verify(frame, p["test_spec"])
            self._repo.set_verification(p["id"], verified=verified,
                                        evidence=evidence, stats=stats)

    def _discover(self, user_id: UUID, frame: dict) -> None:
        if len(frame) < 14:
            return  # a garden this young has nothing to notice yet — stay silent
        existing = [p["hypothesis"] for p in self._repo.all_for(user_id)
                    if p["status"] != "dismissed"]
        dismissed = [p["hypothesis"] for p in self._repo.all_for(user_id)
                     if p["status"] == "dismissed"]
        summary = self._garden_summary(frame)
        for hypothesis, test in self._discovery.propose(summary, existing + dismissed):
            # a dismissed pattern is never resurrected, even reworded — the
            # prompt forbids duplicates and this is the deterministic backstop
            if any(hypothesis.lower() == h.lower() for h in existing + dismissed):
                continue
            self._repo.add(user_id, hypothesis, test)

    def _garden_summary(self, frame: dict[date, dict]) -> str:
        lines = []
        for d in sorted(frame.keys()):
            row = frame[d]
            bits = [d.isoformat()]
            for key in ("energy", "mood", "sleep_hours", "sleep_score", "hrv",
                        "body_battery", "resting_hr", "stress", "cycle_day"):
                if row.get(key) is not None:
                    val = row[key]
                    bits.append(f"{key}={val:.1f}" if isinstance(val, float) else f"{key}={val}")
            if row.get("phase"):
                bits.append(row["phase"])
            if row.get("ran"):
                bits.append("RAN")
            if row.get("meds"):
                bits.append("meds")
            if len(bits) > 1:
                lines.append(" ".join(bits))
        return "\n".join(lines)

    # -- surfacing ------------------------------------------------------------

    def intelligence_context(self, user_id: UUID, now: datetime) -> str | None:
        """The assembler's intelligence slot: verified offers + adopted context.
        Nothing below evidence ever appears here — silence is a feature."""
        patterns = self._repo.all_for(user_id)
        verified = [p for p in patterns if p["status"] == "verified"]
        adopted = [p for p in patterns if p["status"] == "adopted"]
        if not verified and not adopted:
            return None
        parts = []
        if verified:
            parts.append(
                "Verified but not yet discussed — offer ONE, gently, only when the "
                "moment fits (an observation with its evidence, never a verdict; "
                "record her verdict with pattern_response):"
            )
            for p in verified:
                parts.append(f"  [{p['id']}] {p['hypothesis']} — {p['evidence']}")
        if adopted:
            parts.append("Adopted patterns (she confirmed these — let them quietly shape suggestions):")
            for p in adopted:
                parts.append(f"  {p['hypothesis']}")
        return "[The Watcher — long-window patterns]\n" + "\n".join(parts)

    def respond(self, user_id: UUID, pattern_id: UUID, verdict: str,
                note: str | None) -> dict | None:
        """Record her verdict and refresh the vault window."""
        row = self._repo.resolve(user_id, pattern_id, verdict, note)
        if row is not None:
            self._project(user_id)
        return row

    def _project(self, user_id: UUID) -> None:
        if self._vault is None:
            return
        try:
            patterns = self._repo.all_for(user_id)
            sections = [
                ("Wondering (not yet enough evidence)",
                 [p for p in patterns if p["status"] == "proposed"]),
                ("Watching (you said: keep testing)",
                 [p for p in patterns if p["status"] == "watching"]),
                ("Verified — it may bring these up",
                 [p for p in patterns if p["status"] == "verified"]),
                ("Adopted (quietly shaping suggestions)",
                 [p for p in patterns if p["status"] == "adopted"]),
                ("Dismissed (will never come back)",
                 [p for p in patterns if p["status"] == "dismissed"]),
            ]
            lines: list[str] = []
            for title, group in sections:
                if not group:
                    continue
                lines.append(f"## {title}\n")
                for p in group:
                    lines.append(f"- {p['hypothesis']}")
                    if p.get("evidence"):
                        lines.append(f"  - evidence: {p['evidence']}")
                    if not p.get("test_spec") and p["status"] == "proposed":
                        lines.append("  - (can't test this one yet — no computable check)")
                    if p.get("resolution_note"):
                        lines.append(f"  - your note: {p['resolution_note']}")
                lines.append("")
            self._vault.watcher_page("\n".join(lines) if lines else
                                     "Nothing yet. The Watcher is quiet until the data says something.\n")
        except Exception:
            _log.warning("watcher vault projection failed", exc_info=True)


# --- pattern_response tool ---------------------------------------------------

PATTERN_RESPONSE_TOOL: dict = {
    "name": "pattern_response",
    "description": (
        "Record the user's verdict on a Watcher pattern that was offered to them. "
        "Verification proved the numbers; SHE rules on the meaning. adopted = it "
        "may quietly shape suggestions from now on. dismissed = never mention it "
        "again, ever. watching = interesting, keep testing. Get the pattern id "
        "from the [id] in the Watcher context."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern_id": {"type": "string", "description": "UUID from the Watcher context."},
            "verdict": {"type": "string", "enum": ["adopted", "dismissed", "watching"]},
            "note": {"type": "string", "description": "Her reasoning, briefly, if she gave one."},
        },
        "required": ["pattern_id", "verdict"],
    },
}


def handle_pattern_response(user_id: UUID, input_dict: dict, now: datetime, *,
                            watcher: Watcher) -> str:
    pid_raw = str(input_dict.get("pattern_id", "")).strip()
    verdict = str(input_dict.get("verdict", "")).strip()
    if verdict not in ("adopted", "dismissed", "watching"):
        return "verdict must be adopted, dismissed, or watching."
    try:
        pid = UUID(pid_raw)
    except ValueError:
        return f"Invalid pattern_id: {pid_raw!r}"
    note = str(input_dict.get("note", "")).strip() or None
    row = watcher.respond(user_id, pid, verdict, note)
    if row is None:
        return "No pattern with that id."
    if verdict == "dismissed":
        return f"Dismissed — it will never come up again: {row['hypothesis']}"
    if verdict == "watching":
        return f"Kept under watch — it will keep testing quietly: {row['hypothesis']}"
    return f"Adopted — this now quietly shapes suggestions: {row['hypothesis']}"
