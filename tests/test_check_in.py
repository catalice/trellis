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
    def __init__(self):
        self.sent = []
        self.actions = 0

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append(text)
        return SimpleNamespace(chat_id=chat_id, message_id=len(self.sent))

    async def send_chat_action(self, chat_id, action):
        self.actions += 1


class _Reminders:
    def __init__(self, due):
        self.due = due
        self.sent = []
        self.rescheduled = []

    def upcoming(self, user_id, *, hours, now):
        return list(self.due)

    def mark_sent(self, rid):
        self.sent.append(rid)

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
    settings = SimpleNamespace(telegram_allowed_users=(), timezone=timezone.utc, chat_ttl_hours=0)
    uid = uuid4()
    database = SimpleNamespace(list_users=lambda: [(uid, 12345)])
    h = TelegramTrellis.__new__(TelegramTrellis)
    h.settings, h.database, h.assembler, h.reminders = settings, database, assembler, reminders
    h._message_log = None
    h._turn_locks = {}
    import logging
    h.logger = logging.getLogger("test")
    return h, uid


def _reminder(label, kind, *, recurrence=None):
    return Reminder(id=uuid4(), user_id=uuid4(), label=label,
                    remind_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                    status="scheduled", recurrence=recurrence, kind=kind)


class TestCheckInDelivery(unittest.TestCase):
    def test_plain_reminder_posts_label_verbatim_no_model(self):
        rem = _reminder("clean your shoes", "remind")
        reminders, assembler = _Reminders([rem]), _Assembler()
        h, _ = _handler(reminders, assembler)
        bot = _Bot()
        n = asyncio.run(h._deliver_due_reminders_once(SimpleNamespace(bot=bot)))
        self.assertEqual(n, 1)
        self.assertEqual(bot.sent, ["Reminder: clean your shoes"])
        self.assertEqual(assembler.turns, [])
        self.assertEqual(reminders.sent, [rem.id])

    def test_check_in_runs_a_turn_and_sends_the_reply(self):
        rem = _reminder("check in with me about the week ahead", "check_in", recurrence="weekly")
        reminders, assembler = _Reminders([rem]), _Assembler()
        h, uid = _handler(reminders, assembler)
        bot = _Bot()
        asyncio.run(h._deliver_due_reminders_once(SimpleNamespace(bot=bot)))
        self.assertEqual(bot.sent, ["Hey — how did the week go?"])
        self.assertEqual(len(assembler.turns), 1)
        self.assertEqual(assembler.turns[0], _check_in_message(rem.label))
        self.assertIn("not a message from them", assembler.turns[0])
        self.assertIn(rem.label, assembler.turns[0])
        # marked sent + rescheduled BEFORE the turn ran, so a failing turn can't re-fire
        self.assertEqual(reminders.sent, [rem.id])
        self.assertEqual(reminders.rescheduled, [rem.id])

    def test_check_in_turn_failure_is_told_and_not_retried(self):
        rem = _reminder("ask me how the run went", "check_in")

        class _Boom(_Assembler):
            def handle_turn(self, user_id, message):
                raise RuntimeError("api down")

        reminders = _Reminders([rem])
        h, _ = _handler(reminders, _Boom())
        bot = _Bot()
        asyncio.run(h._deliver_due_reminders_once(SimpleNamespace(bot=bot)))
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("ask me how the run went", bot.sent[0])
        self.assertEqual(reminders.sent, [rem.id])


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
