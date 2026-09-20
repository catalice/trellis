"""Self-initiated check-ins (15 Sep 2026): a reminder of kind='check_in' wakes
the oracle instead of posting its label. Same loop, two behaviours."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from trellis.core_telegram import TelegramTrellis, _check_in_message
from trellis.domain_focus_models import Reminder


class _Bot:
    def __init__(self, failures=0):
        self.sent = []
        self.actions = 0
        self.failures = failures            # how many sends fail before Telegram accepts one

    async def send_message(self, chat_id, text, parse_mode=None):
        if self.failures > 0:
            self.failures -= 1
            raise TimeoutError("telegram timed out")
        self.sent.append(text)
        return SimpleNamespace(chat_id=chat_id, message_id=len(self.sent))

    async def send_chat_action(self, chat_id, action):
        self.actions += 1


class _Reminders:
    """The reminder service, with the states a reminder really moves through."""
    def __init__(self, reminders):
        self.rows = {r.id: r for r in reminders}
        self.rescheduled = []

    def _set(self, rid, **changes):
        from dataclasses import replace
        self.rows[rid] = replace(self.rows[rid], **changes)

    def upcoming(self, user_id, *, hours, now):
        return [r for r in self.rows.values() if r.status == "scheduled"]

    def claim(self, rid, *, now):
        if self.rows[rid].status != "scheduled":
            return False
        self._set(rid, status="claimed", claimed_at=now)
        return True

    def ready(self, rid, message):
        self._set(rid, status="executed", message=message)

    def awaiting_delivery(self, user_id):
        return [r for r in self.rows.values() if r.status in ("claimed", "executed")]

    def delivery_failed(self, rid):
        self._set(rid, attempts=self.rows[rid].attempts + 1)
        return self.rows[rid].attempts

    def accepted(self, rid, *, now):
        self._set(rid, status="accepted")

    def undelivered(self, rid):
        self._set(rid, status="undelivered")

    def reschedule(self, user_id, reminder, *, now):
        self.rescheduled.append(reminder.id)


class _Assembler:
    def __init__(self, reply="Hey — how did the week go?"):
        self.turns = []
        self.reply = reply

    def handle_turn(self, user_id, message):
        self.turns.append(message)
        return self.reply


def _handler(reminders, assembler):
    settings = SimpleNamespace(telegram_allowed_users=frozenset({12345}), timezone=timezone.utc, chat_ttl_hours=0)
    uid = uuid4()
    database = SimpleNamespace(list_users=lambda: [(uid, 12345)])
    h = TelegramTrellis.__new__(TelegramTrellis)
    h.settings, h.database, h.assembler, h.reminders = settings, database, assembler, reminders
    h._message_log = None
    h._turn_locks = {}
    h._check_ins_running = set()
    import logging
    h.logger = logging.getLogger("test")
    return h, uid


def _reminder(label, kind, *, recurrence=None, status="scheduled", claimed_minutes_ago=None, message=None):
    now = datetime.now(timezone.utc)
    return Reminder(id=uuid4(), user_id=uuid4(), label=label, remind_at=now - timedelta(minutes=1),
                    status=status, recurrence=recurrence, kind=kind, message=message,
                    claimed_at=(now - timedelta(minutes=claimed_minutes_ago)) if claimed_minutes_ago else None)


def _tick(h, bot):
    return asyncio.run(h._deliver_due_reminders_once(SimpleNamespace(bot=bot)))


class TestCheckInDelivery(unittest.TestCase):
    def test_plain_reminder_posts_label_verbatim_no_model(self):
        rem = _reminder("clean your shoes", "remind")
        reminders, assembler = _Reminders([rem]), _Assembler()
        h, _ = _handler(reminders, assembler)
        bot = _Bot()
        self.assertEqual(_tick(h, bot), 1)
        self.assertEqual(bot.sent, ["Reminder: clean your shoes"])
        self.assertEqual(assembler.turns, [])
        self.assertEqual(reminders.rows[rem.id].status, "accepted")

    def test_check_in_runs_a_turn_and_sends_the_reply(self):
        rem = _reminder("check in with me about the week ahead", "check_in", recurrence="weekly")
        reminders, assembler = _Reminders([rem]), _Assembler()
        h, uid = _handler(reminders, assembler)
        bot = _Bot()
        _tick(h, bot)
        self.assertEqual(bot.sent, ["Hey — how did the week go?"])
        self.assertEqual(assembler.turns, [_check_in_message(rem.label)])
        self.assertIn("not a message from them", assembler.turns[0])
        self.assertEqual(reminders.rows[rem.id].status, "accepted")
        self.assertEqual(reminders.rescheduled, [rem.id])       # rescheduled once, at the claim


class TestSentMeansAccepted(unittest.TestCase):
    """'Sent' used to be written BEFORE delivery: a Telegram timeout left a
    reminder the person never got marked as sent. Now a reminder is claimed,
    made ready, and only 'accepted' once Telegram took it — which is what that
    word can honestly mean (not that it was read)."""

    def test_a_reminder_telegram_refused_is_not_accepted_and_is_retried(self):
        rem = _reminder("clean your shoes", "remind")
        reminders = _Reminders([rem])
        h, _ = _handler(reminders, _Assembler())
        bot = _Bot(failures=2)                                  # Markdown try + plain try both fail
        self.assertEqual(_tick(h, bot), 0)
        self.assertEqual(reminders.rows[rem.id].status, "executed")
        self.assertEqual(reminders.rows[rem.id].attempts, 1)
        self.assertEqual(_tick(h, bot), 1)                      # next tick: delivered
        self.assertEqual(bot.sent, ["Reminder: clean your shoes"])
        self.assertEqual(reminders.rows[rem.id].status, "accepted")

    def test_retries_are_bounded_and_end_as_undelivered(self):
        from trellis.core_telegram import _MAX_DELIVERY_ATTEMPTS
        rem = _reminder("clean your shoes", "remind")
        reminders = _Reminders([rem])
        h, _ = _handler(reminders, _Assembler())
        bot = _Bot(failures=10_000)
        for _ in range(_MAX_DELIVERY_ATTEMPTS + 3):
            _tick(h, bot)
        self.assertEqual(reminders.rows[rem.id].status, "undelivered")
        self.assertEqual(reminders.rows[rem.id].attempts, _MAX_DELIVERY_ATTEMPTS)

    def test_a_claim_lost_to_another_worker_is_left_alone(self):
        rem = _reminder("clean your shoes", "remind")
        reminders = _Reminders([rem])
        reminders.claim = lambda rid, *, now: False
        h, _ = _handler(reminders, _Assembler())
        bot = _Bot()
        _tick(h, bot)
        self.assertEqual(bot.sent, [])


class TestACheckInsActionsNeverRunTwice(unittest.TestCase):
    """Delivery may be retried; the turn that produced the message may not —
    it may already have done things."""

    def test_a_failed_send_resends_the_stored_reply_without_a_second_turn(self):
        rem = _reminder("check in about the week", "check_in")
        reminders, assembler = _Reminders([rem]), _Assembler()
        h, _ = _handler(reminders, assembler)
        bot = _Bot(failures=2)
        _tick(h, bot)
        self.assertEqual(reminders.rows[rem.id].message, "Hey — how did the week go?")   # stored before sending
        _tick(h, bot)
        self.assertEqual(bot.sent, ["Hey — how did the week go?"])
        self.assertEqual(len(assembler.turns), 1)                                         # ONE turn, ever

    def test_a_turn_that_raised_is_told_and_never_rerun(self):
        rem = _reminder("ask me how the run went", "check_in")

        class _Boom(_Assembler):
            def handle_turn(self, user_id, message):
                self.turns.append(message)
                raise RuntimeError("api down")

        assembler = _Boom()
        reminders = _Reminders([rem])
        h, _ = _handler(reminders, assembler)
        bot = _Bot()
        _tick(h, bot)
        _tick(h, bot)
        self.assertEqual(len(assembler.turns), 1)
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("ask me how the run went", bot.sent[0])
        self.assertIn("haven't run it again", bot.sent[0])

    def test_a_check_in_interrupted_by_a_crash_is_reported_not_rerun(self):
        """Claimed long ago, no message: the process died mid-turn. What it did is unknown."""
        rem = _reminder("weekly review", "check_in", status="claimed", claimed_minutes_ago=30)
        reminders, assembler = _Reminders([rem]), _Assembler()
        h, _ = _handler(reminders, assembler)
        bot = _Bot()
        _tick(h, bot)
        self.assertEqual(assembler.turns, [])
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("weekly review", bot.sent[0])
        self.assertIn("interrupted", bot.sent[0])
        self.assertEqual(reminders.rows[rem.id].status, "accepted")

    def test_a_plain_reminder_claimed_before_a_crash_is_still_delivered(self):
        """A possible duplicate is better than a miss."""
        rem = _reminder("clean your shoes", "remind", status="claimed", claimed_minutes_ago=30)
        reminders = _Reminders([rem])
        h, _ = _handler(reminders, _Assembler())
        bot = _Bot()
        _tick(h, bot)
        self.assertEqual(bot.sent, ["Reminder: clean your shoes"])

    def test_a_check_in_still_running_is_not_mistaken_for_a_crash(self):
        rem = _reminder("weekly review", "check_in", status="claimed", claimed_minutes_ago=30)
        reminders = _Reminders([rem])
        h, _ = _handler(reminders, _Assembler())
        h._check_ins_running.add(rem.id)
        bot = _Bot()
        _tick(h, bot)
        self.assertEqual(bot.sent, [])


class TestFocusAddCheckIn(unittest.TestCase):
    def test_check_in_flag_sets_kind(self):
        from zoneinfo import ZoneInfo
        from trellis.domain_focus_tool import handle_set_reminder

        saved = []

        class _Svc:
            def all_scheduled(self, uid): return []
            def set(self, uid, label, remind_at, *, task_id=None, recurrence=None, kind="remind", now):
                r = Reminder(id=uuid4(), user_id=uid, label=label, remind_at=remind_at,
                             status="scheduled", recurrence=recurrence, kind=kind)
                saved.append(r)
                return r

        now = datetime.now(timezone.utc)
        future = (now + timedelta(days=1)).astimezone(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%dT%H:%M")
        out = handle_set_reminder(uuid4(), {"label": "check in about the week", "remind_at": future,
                                            "recurrence": "weekly", "check_in": True},
                                  now, reminder_service=_Svc(), tz=ZoneInfo("Europe/Madrid"))
        self.assertTrue(out.startswith("Check-in set:"))
        self.assertEqual(saved[0].kind, "check_in")
        out = handle_set_reminder(uuid4(), {"label": "clean shoes", "remind_at": future},
                                  now, reminder_service=_Svc(), tz=ZoneInfo("Europe/Madrid"))
        self.assertTrue(out.startswith("Reminder set:"))
        self.assertEqual(saved[1].kind, "remind")


class TestCheckInIsItsOwnKind(unittest.TestCase):
    """Review 17 Sep: a check-in and a plain reminder sharing a label are not
    duplicates of each other, and a fired check-in reads as one."""

    def _svc(self, existing):
        class _Svc:
            saved = []
            def all_scheduled(self, uid):
                return existing
            def set(self, uid, label, remind_at, *, task_id=None, recurrence=None, kind="remind", now):
                r = Reminder(id=uuid4(), user_id=uid, label=label, remind_at=remind_at,
                             status="scheduled", recurrence=recurrence, kind=kind)
                self.saved.append(r)
                return r
            def recent(self, uid, *, limit=10):
                return existing
        return _Svc()

    def test_dup_guard_is_per_kind(self):
        from zoneinfo import ZoneInfo
        from trellis.domain_focus_tool import handle_set_reminder
        tz = ZoneInfo("Europe/Madrid")
        now = datetime.now(timezone.utc)
        at = (now + timedelta(hours=2)).astimezone(tz).strftime("%Y-%m-%dT%H:%M")
        plain = _reminder("look at the week", "remind")
        svc = self._svc([plain])
        reply = handle_set_reminder(uuid4(), {"label": "look at the week", "remind_at": at, "check_in": True},
                                    now, reminder_service=svc, tz=tz)
        self.assertNotIn("already existed", reply)
        reply = handle_set_reminder(uuid4(), {"label": "look at the week", "remind_at": at},
                                    now, reminder_service=svc, tz=tz)
        self.assertIn("reminder with this label already existed", reply)

    def test_recent_list_names_the_kind(self):
        from zoneinfo import ZoneInfo
        from trellis.domain_focus_tool import _view_reminders, _FocusReads
        fired = _reminder("ask me how the run went", "check_in")
        fired = Reminder(**{**fired.__dict__, "status": "sent"})
        svc = self._svc([fired])
        svc.all_scheduled = lambda uid: []
        ctx = _FocusReads(task_service=None, goal_service=None, capture_service=None,
                          effort_service=None, reminder_service=svc, tz=ZoneInfo("Europe/Madrid"))
        out = _view_reminders(uuid4(), {}, datetime.now(timezone.utc), ctx)
        self.assertIn("check-in: ask me how the run went", out)
