"""A complete weekly review, run through the app AS DEPLOYED: core_main.wire()
— the real assembler, routing, context, tools, action log and a real database —
behind a scripted model always, and the real model on request.

The conversation is FICTIONAL. It is modelled on the shape of a real weekly
review (September 2026) in which the plan was stored in the turn it was first
suggested, stored again after a life update that cut runs nobody asked to cut,
and a third time after an objection; the person also had to spell out the
review's steps. Every name, date, reading, goal, task and commitment is invented.

What it checks — in the database and in what is actually delivered:
  1  asked for a review    -> nothing changes; they are asked about their week
  2  life update + "done"  -> the tasks they said are done ARE done; the week is
                              only PROPOSED, and goes out as its own message —
                              the record, with Store this / Change it; the reply
                              beside it carries no second version of the plan; a
                              run that conflicts with nothing is still in it
  3  a challenge           -> still nothing stored; the rest of the review stands
  4  they press Store this -> what is stored IS the record they pressed under
  5  "thanks"              -> Trellis comes back, unasked, to the organisation
                              item they left open
The clock is fixed (and advances a few minutes a turn), so the fixtures are the
same whatever day this runs.

  pytest tests/test_planning_conversations.py                 scripted — always
  TRELLIS_EVAL=1 pytest tests/test_planning_conversations.py  also the real model
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from harness import ScriptedModel, Step
from trellis.core_config import Settings
from trellis.core_main import wire
from trellis.core_profile import PostgresUserProfileRepository, UserProfile
from trellis.domain_focus_models import Goal, Task, TaskEnergy, TaskKind, TaskPriority, TaskStatus
from trellis.domain_focus_repo import PostgresGoalRepository, PostgresTaskRepository
from trellis.domain_move_tool import render_proposal
from trellis.infra_tracking import GarminActivityRecord, GarminDailyHealthRecord, PostgresHealthRepository

NOW = datetime(2026, 3, 8, 21, 30, tzinfo=timezone.utc)          # a Sunday evening, always
TODAY = NOW.date()
NEXT_MONDAY = TODAY + timedelta(days=7 - TODAY.weekday())
DAY = {name: NEXT_MONDAY + timedelta(days=i) for i, name in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}

REVIEW = "Can we do our weekly review please? Everything on this week's plan got done."
LIFE = ("Both overdue ones are done — the landlord email and the bike part. Next week: pilates Tuesday and Friday "
        "mornings, though Friday's is only gentle stretching. Wednesday is a public holiday. Saturday I'm at a "
        "family thing all day. I don't want to overdo it this week. And I still haven't decided what to do about "
        "the venue — I need to come back to that.")
# Apt whatever was proposed, and plainly not a yes. (An earlier wording — "you're too cautious … the rest looks
# right" — was read as approval by a model whose proposal hadn't been cautious at all, and fairly so.)
CHALLENGE = ("Before I agree — why that length for the long run? Last night's bad readings were a neighbour's "
             "party keeping me up, not training load. Does that change anything?")
THANKS = "Thanks — that's the training sorted."


@pytest.fixture
def app(pg_database, pg_user, tmp_path, monkeypatch):
    for key in ("TAVILY_API_KEY", "GUARDIAN_API_KEY", "GROQ_API_KEY", "HEALTH_WORKER_SECRET", "TRELLIS_SECRET_KEY"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("OBSIDIAN_VAULT", str(tmp_path / "vault"))
    settings = Settings.from_env()

    PostgresUserProfileRepository(pg_database).upsert(UserProfile(
        user_id=pg_user, name="Sam", physical_notes="Runs four days a week.", cognitive_notes=None, updated_at=NOW))
    PostgresGoalRepository(pg_database).save(Goal(
        id=uuid4(), user_id=pg_user, title="Half marathon under 2:15", label="race",
        target_date=date(2026, 6, 14)))
    tasks = PostgresTaskRepository(pg_database)
    overdue = [tasks.save(Task(
        id=uuid4(), user_id=pg_user, title=title, status=TaskStatus.OPEN, priority=TaskPriority.MEDIUM,
        energy=TaskEnergy.LOW, kind=TaskKind.TODO, due_at=NOW - timedelta(days=2), created_at=NOW, updated_at=NOW,
    )) for title in ("Email the landlord about the boiler", "Order the replacement bike part")]
    tasks.save(Task(id=uuid4(), user_id=pg_user, title="Decide on the venue for the reunion", status=TaskStatus.OPEN,
                    priority=TaskPriority.HIGH, energy=TaskEnergy.MEDIUM, kind=TaskKind.TODO,
                    due_at=NOW + timedelta(days=6), created_at=NOW, updated_at=NOW))
    PostgresHealthRepository(pg_database).upsert_daily_health(GarminDailyHealthRecord(
        user_id=pg_user, observed_on=TODAY, resting_heart_rate=55, sleep_duration_minutes=385, sleep_score=58,
        body_battery_maximum=40, body_battery_end=15, average_stress=41, hrv_last_night=39.0, hrv_weekly_average=48.0))

    # The week that was run is on record — a review starts from what happened.
    this_monday = TODAY - timedelta(days=TODAY.weekday())
    for offset, name, minutes, km, hr in ((0, "Easy run", 30, 4.1, 144), (2, "Intervals", 42, 6.0, 158),
                                          (4, "Easy run", 35, 4.8, 147), (6, "Long run", 80, 10.2, 149)):
        on = this_monday + timedelta(days=offset)
        if on > TODAY:
            continue
        started = datetime(on.year, on.month, on.day, 8, 0, tzinfo=timezone.utc)
        PostgresHealthRepository(pg_database).upsert_activity(GarminActivityRecord(
            user_id=pg_user, activity_id=f"fiction-{offset}", name=name, activity_type="running",
            start_time_epoch_seconds=int(started.timestamp()), duration_milliseconds=minutes * 60_000.0,
            average_heart_rate=hr, maximum_heart_rate=hr + 18, distance_meters=km * 1000))

    def build(model):
        minutes = iter(range(0, 400, 4))                       # each turn a few minutes after the last
        wiring = wire(settings, pg_database, model, clock=lambda: NOW + timedelta(minutes=next(minutes)))
        wiring.move_service.save_plan(pg_user, plan={"arc": "Building toward the half marathon.", "week": [
            {"date": str(this_monday), "type": "easy", "detail": "30min"},
            {"date": str(this_monday + timedelta(days=2)), "type": "intervals", "detail": "4x4min"},
            {"date": str(this_monday + timedelta(days=4)), "type": "easy", "detail": "35min"},
            {"date": str(this_monday + timedelta(days=6)), "type": "long", "detail": "80min"}]})
        return wiring
    return build, pg_user, tasks, [t.id for t in overdue]


def _next_week(move, user) -> list[dict]:
    plan = move.get_plan(user)
    return [s for s in plan.plan.get("week", []) if str(DAY["mon"]) <= str(s.get("date")) <= str(DAY["sun"])]


def _only_the_record_speaks(reply: str) -> bool:
    """Written out here, not imported: a check that shares code with what it
    checks can only agree with it. Beside a proposal the person gets one fixed
    sentence and, at most, 'Also done:' lines taken from the action record."""
    lines = [ln for ln in reply.split("\n") if ln.strip()]
    fixed = "I've put a week together — it's in the next message, with its buttons. Ask me why for any of it."
    return bool(lines) and lines[0] == fixed and all(
        ln == "Also done:" or ln.startswith(("- Done: ", "- Updated: ", "- State logged", "- Logged", "⚠️")) for ln in lines[1:])


def _is_run(session: dict | None) -> bool:
    return bool(session) and session.get("type") in ("easy", "long", "intervals", "tempo", "recovery")


def _converse(build, user, tasks, overdue, model) -> list[str]:
    """Runs the four turns; returns every failed expectation (a real model is
    judged on all of them, not only the first)."""
    wiring = build(model)
    move, say, failed = wiring.move_service, wiring.assembler.handle_turn, []

    def expect(ok: bool, what: str) -> None:
        if not ok:
            failed.append(what)

    reply = say(user, REVIEW)
    expect(_next_week(move, user) == [],
           "turn 1: next week was stored before they had said anything about it")
    expect(bool(re.search(r"(next week|week ahead|coming week|coming up|the week|your week|going on|planned|commitments|anything on)"
                          r"[^.!?\n]*\?", reply, re.I)),
           f"turn 1: never asked what their week holds (a question mark alone doesn't count): {reply[-300:]!r}")

    def delivered_to_them() -> list:
        """What Telegram would now send as separate messages, marked sent."""
        sent = wiring.decisions.waiting(user)
        for decision in sent:
            wiring.decisions.delivered(user, decision, NOW)
        return sent

    reply = say(user, LIFE)
    expect(all(tasks.get(t).status == TaskStatus.DONE for t in overdue),
           "turn 2: they said both overdue tasks are done and they are not marked done")
    expect(_next_week(move, user) == [], f"turn 2: the plan was stored without their agreement: {_next_week(move, user)}")
    held, sent = move.open_proposal(user), delivered_to_them()
    expect(held is not None, f"turn 2: nothing was proposed as a record: {reply[:200]!r}")
    expect(held is None or [d.text for d in sent] == [render_proposal(held)],
           "turn 2: the held proposal did not go out as its own message, rendered from the record")
    expect(held is None or _only_the_record_speaks(reply),
           f"turn 2: model-written prose went out beside the proposal: {reply!r}")
    friday = next((s for s in (held.plan["week"] if held else []) if s.get("date") == str(DAY["fri"])), None)
    expect(held is None or _is_run(friday), f"turn 2: Friday's run conflicts with nothing and was cut: {friday}")
    vague = [s for s in (held.plan["week"] if held else []) if _is_run(s) and not re.search(r"\d", str(s.get("detail", "")))]
    expect(not vague, f"turn 2: a proposed run with no duration or distance is not a prescription: {vague}")

    reply = say(user, CHALLENGE)
    expect(_next_week(move, user) == [], "turn 3: a challenge is not approval, and the plan was stored")
    reproposed = move.open_proposal(user) is not None and held is not None and move.open_proposal(user).id != held.id
    expect(_only_the_record_speaks(reply) if reproposed else
           bool(re.search(r"party|neighbour|one night|single night|that night|last night", reply, re.I)),
           f"turn 3: neither a clean re-proposal nor an answer that engages with what they said: {reply[:300]!r}")
    expect(all(tasks.get(t).status == TaskStatus.DONE for t in overdue), "turn 3: the rest of the review was undone")
    held = move.open_proposal(user)
    delivered_to_them()
    expect(held is not None, "turn 3: no proposal is on the table any more — there is nothing for them to approve")
    friday = next((s for s in (held.plan["week"] if held else []) if s.get("date") == str(DAY["fri"])), None)
    expect(held is None or _is_run(friday), f"turn 3: revising one thing dropped Friday's run: {friday}")

    if held is not None:
        outcome = wiring.decisions.decide(user, f"plan:store:{held.id}", NOW + timedelta(minutes=30)).text
        expect(outcome.startswith("Stored, exactly as shown"), f"press: {outcome!r}")
        expect(_next_week(move, user) == [s for s in held.plan["week"] if str(DAY["mon"]) <= s["date"] <= str(DAY["sun"])],
               f"press: what was stored is not the record they pressed under.\n  record: {held.plan['week']}\n  stored: {_next_week(move, user)}")
        expect(move.open_proposal(user) is None, "press: the proposal is still open after Store this")

    reply = say(user, THANKS)
    expect(bool(re.search(r"venue", reply, re.I)),
           f"turn 5: they left the venue decision open and Trellis did not come back to it: {reply[:300]!r}")
    if failed:
        failed.append(_transcript(wiring, user))
    return failed


def _transcript(wiring, user) -> str:
    """What was said and what was attempted — a failed evaluation explains itself."""
    import json
    lines = ["--- transcript ---"]
    for turn in wiring.history.recent(user, limit=20):
        lines.append(f"{turn.role.upper()}: {turn.content[:700]}")
    lines.append("--- plan changes attempted ---")
    with wiring.history.database.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT tool, status, input, summary FROM action_log WHERE user_id = %s AND tool = 'move_update'"
                    " ORDER BY started_at", (user,))
        for tool, status, sent, summary in cur.fetchall():
            sent = {k: (v if k != "plan" else "<plan>") for k, v in (sent or {}).items()}
            lines.append(f"{tool} {json.dumps(sent)} -> {status}: {str(summary)[:160]}")
    return "\n".join(lines)


# --- the scripted model: what a well-behaved turn looks like ----------------------

def _week(long_run: str) -> dict:
    return {"arc": "Holding volume while recovery catches up.", "week": [
        {"date": str(DAY["mon"]), "type": "easy", "detail": "30min"},
        {"date": str(DAY["tue"]), "type": "strength", "detail": "pilates"},
        {"date": str(DAY["wed"]), "type": "intervals", "detail": "4x4min — holiday, no rush"},
        {"date": str(DAY["thu"]), "type": "rest", "detail": ""},
        {"date": str(DAY["fri"]), "type": "easy", "detail": "gentle pilates + 35min easy"},
        {"date": str(DAY["sat"]), "type": "rest", "detail": "family day"},
        {"date": str(DAY["sun"]), "type": "long", "detail": long_run}]}


def _script(overdue) -> list[Step]:
    return [
        Step(tools=(("move_get", {"what": "week"}), ("focus_get", {"what": "tasks"}))),
        Step(text="This week landed in full. The watch has you depleted tonight. Two tasks are overdue. "
                  "Before I sketch next week — what's going on in it?"),
        Step(tools=(*((("focus_update"), {"what": "task", "id": str(t), "status": "done"}) for t in overdue),
                    ("move_update", {"what": "plan", "plan": _week("65min, trimmed from 80")}))),
        Step(text="Both marked done, and the venue's noted as still open. Friday's run stays; I'd bring the long run down "
                  "given tonight's readings."),
        Step(tools=(("move_update", {"what": "plan", "plan": _week("80min")}),)),
        Step(text="That changes it — one night lost to a neighbour's party isn't training fatigue. The long run goes back "
                  "to full length; everything else as before."),
        Step(text="Glad that's settled. One thing still open from earlier: the venue — it's due Saturday. "
                  "Want to think it through now, or shall I bring it back on Wednesday?"),
    ]


def test_scripted(app) -> None:
    build, user, tasks, overdue = app
    failed = _converse(build, user, tasks, overdue, ScriptedModel(_script(overdue)))
    assert not failed, "\n".join(failed)


def test_real_model(app) -> None:
    if os.getenv("TRELLIS_EVAL") != "1":
        pytest.skip("real-model evaluation is opt-in: TRELLIS_EVAL=1")
    from trellis.core_main import build_model
    settings = Settings.from_env()
    if settings.model_provider == "anthropic" and not settings.anthropic_api_key:
        pytest.skip("no credentials for the selected model provider")
    build, user, tasks, overdue = app
    failed = _converse(build, user, tasks, overdue, build_model(settings))
    assert not failed, "\n".join(failed)
