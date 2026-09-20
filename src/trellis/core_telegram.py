from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from trellis.core_assembler import Assembler
from trellis.core_config import Settings
from trellis.infra_memory import MemoryIndex
from trellis.infra_postgres import PostgresDatabase
from trellis.domain_focus_service import ReminderService

# (audio_bytes) -> transcript
Transcriber = Callable[[bytes], str]

# NOTE: check-ins are NOT hardcoded here. When the user wants a morning/evening
# check-in or a weekly review, the oracle creates a real recurring reminder
# (focus_add what='reminder', recurrence daily/weekly/monthly/yearly)
# — persisted, user-owned, editable, and surviving restarts. There is deliberately
# no baked-in ping schedule; that was removed because it wasn't tied to the user's
# choice and silently died on restart.
#
# Two kinds fire from the same loop (the user's design, 15 Sep 2026): kind='remind'
# posts the label back verbatim, no model; kind='check_in' runs a full oracle
# turn with the label as Trellis's own instruction and sends what it writes —
# the first time Trellis speaks unprompted with generated words. It speaks
# once; then it's their turn. No follow-up nagging.


def _check_in_message(label: str) -> str:
    """What the oracle receives when a check-in fires. It arrives on the user
    side of the conversation, so it says plainly that it isn't them speaking."""
    return (
        "[Scheduled check-in. This is the instruction they set for you to run "
        f"at this time, not a message from them: \"{label}\". "
        "Read what you need, then speak to them first — as if you'd walked in.]"
    )


class _ChatShim:
    """Just enough of a Chat for the typing keepalive when there's no Update."""
    def __init__(self, bot, chat_id) -> None:
        self._bot, self._chat_id = bot, chat_id

    async def send_action(self, action: str) -> None:
        await self._bot.send_chat_action(chat_id=self._chat_id, action=action)


def make_transcriber(groq_client, model: str = "whisper-large-v3-turbo") -> Transcriber:
    def transcribe(audio: bytes) -> str:
        response = groq_client.audio.transcriptions.create(
            file=("voice.ogg", audio),
            model=model,
        )
        return response.text.strip()
    return transcribe


class TelegramTrellis:
    def __init__(
        self,
        settings: Settings,
        database: PostgresDatabase,
        assembler: Assembler,
        reminders: ReminderService | None = None,
        transcriber: Transcriber | None = None,
        memory: MemoryIndex | None = None,
        garmin_sync: Callable[[], None] | None = None,
        watcher_tick: Callable[[], None] | None = None,
        message_log=None,        # history repo: telegram message registry (chat sweep)
    ):
        self.settings = settings
        self.database = database
        self.assembler = assembler
        self.reminders = reminders
        self.transcriber = transcriber
        self.memory = memory
        self._garmin_sync = garmin_sync
        self._watcher_tick = watcher_tick
        self._message_log = message_log
        self._chat_ttl_hours = getattr(settings, "chat_ttl_hours", 0)
        self._marker_hour = getattr(settings, "marker_hour", -1)
        self._watcher_task: asyncio.Task | None = None
        self._reminder_delivery_task: asyncio.Task | None = None
        self._garmin_sync_task: asyncio.Task | None = None
        self._marker_task: asyncio.Task | None = None
        self._chat_sweep_task: asyncio.Task | None = None
        # One lock per user: turns are processed strictly in arrival order, so a
        # rapid second message always sees the first exchange in history (and
        # replies can't interleave or land in scrambled order).
        self._turn_locks: dict = {}
        self.logger = logging.getLogger(__name__)

    def build(self) -> Application:
        application = (
            Application.builder()
            .token(self.settings.telegram_bot_token)
            # The library defaults to 5s for connect/read/write. A brief network
            # stall (Docker on a laptop, a Wi-Fi blip) once timed out a reply
            # send and the whole turn was dropped — 30s rides that out.
            .connect_timeout(_TELEGRAM_TIMEOUT)
            .read_timeout(_TELEGRAM_TIMEOUT)
            .write_timeout(_TELEGRAM_TIMEOUT)
            .post_init(self._post_init)
            .post_shutdown(self._post_shutdown)
            .build()
        )
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.message)
        )
        application.add_handler(MessageHandler(filters.VOICE, self.voice))
        application.add_error_handler(self._on_error)
        return application

    async def _on_error(self, update, context) -> None:
        """Registered handler: polling/network hiccups get one WARNING line
        instead of a naked 'No error handlers are registered' traceback."""
        self.logger.warning("telegram error: %s", context.error)

    async def _post_init(self, application: Application) -> None:
        if self.reminders is not None:
            self._reminder_delivery_task = asyncio.create_task(
                self._deliver_due_reminders_loop(application)
            )
        if self._garmin_sync is not None:
            self._garmin_sync_task = asyncio.create_task(self._garmin_sync_loop())
        if self._marker_hour >= 0 and self._message_log is not None:
            self._marker_task = asyncio.create_task(self._marker_loop(application))
        if self._chat_ttl_hours > 0 and self._message_log is not None:
            self._chat_sweep_task = asyncio.create_task(
                self._chat_sweep_loop(application)
            )
        if self._watcher_tick is not None:
            self._watcher_task = asyncio.create_task(self._watcher_loop())

    async def _post_shutdown(self, application: Application) -> None:
        for attr in ("_reminder_delivery_task", "_garmin_sync_task", "_watcher_task",
                     "_marker_task", "_chat_sweep_task"):
            task = getattr(self, attr)
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            setattr(self, attr, None)

    async def _garmin_sync_loop(self) -> None:
        """Refresh Garmin data for connected users (health/readiness + recent runs).
        Every 6 hours, not daily: a 24h cadence anchored to the last restart meant
        mornings served yesterday's sleep/HRV as "readiness". Blocking work runs off
        the event loop; failures never crash the bot. A short initial delay keeps
        startup snappy."""
        await asyncio.sleep(60)
        while True:
            try:
                await asyncio.to_thread(self._garmin_sync)
            except Exception:
                self.logger.exception("Garmin sync failed")
            await asyncio.sleep(6 * 3600)

    async def _watcher_loop(self) -> None:
        """Daily tick for the Watcher: verification every tick (cheap, pure
        Python), discovery only when it's been quiet a week (one Claude call).
        First tick after a short startup delay so a restart never skips a day."""
        await asyncio.sleep(120)
        while True:
            try:
                await asyncio.to_thread(self._watcher_tick)
            except Exception:
                self.logger.exception("Watcher tick failed")
            await asyncio.sleep(24 * 3600)

    async def _marker_loop(self, application: Application) -> None:
        """The memory-horizon marker (the user's design): one line each morning —
        everything below it is verbatim memory; older lives in the records.
        Yesterday's marker is deleted so they never pile up."""
        from datetime import timedelta
        while True:
            now_local = datetime.now(self.settings.timezone)
            target = now_local.replace(hour=self._marker_hour, minute=0,
                                       second=0, microsecond=0)
            if target <= now_local:
                target += timedelta(days=1)
            await asyncio.sleep((target - now_local).total_seconds())
            try:
                users = await asyncio.to_thread(self.database.list_users)
                for user_id, tg_id in users:
                    if not self._is_allowed(tg_id):
                        continue
                    old = await asyncio.to_thread(self._message_log.get_marker, tg_id)
                    sent = await application.bot.send_message(
                        chat_id=tg_id,
                        text=("☀️ — everything below this I remember "
                              "word-for-word; older, ask me to look it up."),
                    )
                    await asyncio.to_thread(
                        self._message_log.set_marker, tg_id, sent.message_id
                    )
                    if old:
                        try:
                            await application.bot.delete_message(chat_id=tg_id, message_id=old)
                        except Exception:
                            pass
            except Exception:
                self.logger.exception("marker loop failed")

    async def _chat_sweep_loop(self, application: Application) -> None:
        """The user's design: the visible chat matches the verbatim window. Messages
        older than the TTL are deleted (Telegram allows deletion only within
        48h, so undeletable stragglers are forgotten, not retried)."""
        from datetime import timedelta
        await asyncio.sleep(300)
        while True:
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=self._chat_ttl_hours)
                hard = datetime.now(timezone.utc) - timedelta(hours=47, minutes=30)
                swept = 0
                sweepable = await asyncio.to_thread(
                    self._message_log.sweepable_telegram_messages, older_than=cutoff
                )
                for chat_id, message_id, sent_at in sweepable:
                    if sent_at > hard:
                        try:
                            await application.bot.delete_message(chat_id=chat_id, message_id=message_id)
                            swept += 1
                        except Exception:
                            pass   # already gone, or refused — forget either way
                    await asyncio.to_thread(
                        self._message_log.forget_telegram_message, chat_id, message_id
                    )
                if swept:
                    self.logger.info("chat sweep: %d message(s) aged out", swept)
            except Exception:
                self.logger.exception("chat sweep failed")
            await asyncio.sleep(6 * 3600)

    async def _deliver_due_reminders_loop(self, application: Application) -> None:
        while True:
            try:
                await self._deliver_due_reminders_once(application)
            except Exception:
                self.logger.exception("Reminder delivery loop failed")
            await asyncio.sleep(15)

    async def _deliver_due_reminders_once(self, application: Application) -> int:
        if self.reminders is None:
            return 0
        delivered = 0
        now = datetime.now(timezone.utc)
        users = await asyncio.to_thread(self.database.list_users)
        for user_id, telegram_user_id in users:
            if not self._is_allowed(telegram_user_id):
                continue
            due = await asyncio.to_thread(
                self.reminders.upcoming, user_id, hours=0, now=now
            )
            for reminder in due:
                # Marked sent BEFORE delivery for both kinds: a check-in that
                # fails mid-turn must not re-fire every 15s, one oracle call
                # a time, until it succeeds.
                await asyncio.to_thread(self.reminders.mark_sent, reminder.id)
                if reminder.recurrence:
                    await asyncio.to_thread(
                        self.reminders.reschedule, user_id, reminder, now=now
                    )
                if reminder.kind == "check_in":
                    await self._run_check_in(application, user_id, telegram_user_id, reminder.label)
                else:
                    sent = await application.bot.send_message(
                        chat_id=telegram_user_id,
                        text=f"Reminder: {reminder.label}",
                    )
                    self._record_msg(sent)
                delivered += 1
        return delivered

    async def _run_check_in(self, application: Application, user_id, telegram_user_id, label: str) -> None:
        """A reminder that wakes Trellis instead of the user: one ordinary
        oracle turn, the label as its instruction, the reply sent as-is. Takes
        the user's turn lock so it can't interleave with a message they're
        mid-sending. Lands in history like any turn — the user side is marked
        as the scheduled instruction, so the next turn knows who spoke first."""
        lock = self._turn_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            typing = asyncio.create_task(self._typing_keepalive(
                _ChatShim(application.bot, telegram_user_id)
            ))
            try:
                reply = await asyncio.to_thread(
                    self.assembler.handle_turn, user_id, _check_in_message(label)
                )
            except Exception:
                self.logger.exception("Check-in turn failed for user %s", user_id)
                reply = (f"I was going to check in ({label}) but something went wrong "
                         "on my side — say the word and I'll do it now.")
            finally:
                typing.cancel()
                try:
                    await typing
                except (asyncio.CancelledError, Exception):
                    pass
            for chunk in _chunk_message(reply or ""):
                if not chunk.strip():
                    continue
                try:
                    self._record_msg(await application.bot.send_message(
                        chat_id=telegram_user_id, text=chunk, parse_mode="Markdown"))
                except Exception:
                    try:
                        self._record_msg(await application.bot.send_message(
                            chat_id=telegram_user_id, text=chunk))
                    except Exception:
                        self.logger.warning("Failed to deliver check-in", exc_info=True)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self._user(update) is None:
            chat = update.effective_chat
            if chat is not None and chat.type == "private":
                # Setup help, nothing private: their own id is what the
                # allowlist needs.
                await update.message.reply_text(
                    f"This Trellis isn't set up for you. Your Telegram id is "
                    f"{update.effective_user.id} — its owner adds it to TELEGRAM_ALLOWED_USERS."
                )
            return
        await update.message.reply_text(
            "Trellis is ready. Send tasks, ideas, questions or a full brain dump. "
            "I'll preserve the original and organise what's useful."
        )

    async def message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._user(update)
        if user_id is None:
            return
        await self._respond(update, user_id, update.message.text)

    async def voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._user(update)
        if user_id is None:
            return
        if self.transcriber is None:
            await update.message.reply_text(
                "Voice notes aren't set up right now — send it as text instead."
            )
            return

        await update.message.chat.send_action("typing")
        try:
            voice_file = await update.message.voice.get_file()
            audio = bytes(await voice_file.download_as_bytearray())
            transcript = await asyncio.to_thread(self.transcriber, audio)
        except Exception:
            self.logger.exception("Voice transcription failed for user %s", user_id)
            await update.message.reply_text(
                "Couldn't transcribe that voice note — try again or send it as text."
            )
            return

        if not transcript:
            await update.message.reply_text(
                "That voice note came through empty — try again?"
            )
            return

        await self._respond(update, user_id, transcript)

    def _record_msg(self, message) -> None:
        """Register a Telegram message id for the chat sweep. Never raises."""
        if self._message_log is None or message is None:
            return
        try:
            self._message_log.record_telegram_message(message.chat_id, message.message_id)
        except Exception:
            self.logger.warning("message record failed", exc_info=True)

    async def _respond(self, update: Update, user_id, text: str) -> None:
        lock = self._turn_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            self._record_msg(update.message)
            # Visible feedback for the whole turn: Telegram's typing indicator
            # only lasts ~5s, so it's re-sent until the reply is ready. It is
            # best-effort — a failed send costs nothing. (A "working…" message
            # bubble used to do this job; sending it sat outside any error
            # handling, and one 5s timeout on that send dropped the entire turn.)
            typing = asyncio.create_task(self._typing_keepalive(update.message.chat))
            try:
                reply = await asyncio.to_thread(
                    self.assembler.handle_turn, user_id, text
                )
            except Exception:
                self.logger.exception("Oracle failed for user %s", user_id)
                # NEVER claim "nothing was changed": tool calls from earlier in
                # the turn may already have committed before the failure.
                reply = ("Something went wrong mid-turn. Anything I'd already "
                         "done before the error is saved — ask me what changed "
                         "before redoing it.")
            finally:
                typing.cancel()
                try:
                    await typing
                except (asyncio.CancelledError, Exception):
                    pass

            final = reply or "Something went wrong — no response was generated. Please try again."
            await self._deliver(update, final)
            await self._maybe_alert_embed_failures(update)

    async def _typing_keepalive(self, chat, interval: float = 4.0) -> None:
        """Keep the typing indicator alive until cancelled. Never raises."""
        while True:
            try:
                await chat.send_action("typing")
            except Exception:
                pass
            await asyncio.sleep(interval)

    async def _deliver(self, update: Update, text: str) -> None:
        """Land the reply no matter what. Over-limit texts are CHUNKED at
        paragraph boundaries first (Telegram hard-caps messages at 4096 chars —
        a 6,439-char reply once vanished into Message_too_long with the user
        told nothing). Then: Markdown first; if Telegram can't parse it (an
        unbalanced * or _) fall back to PLAIN text — so a reply is never lost
        to a formatting quirk."""
        for chunk in _chunk_message(text):
            await self._deliver_once(update, chunk)

    async def _deliver_once(self, update: Update, text: str) -> None:
        try:
            self._record_msg(await update.message.reply_text(text, parse_mode="Markdown"))
        except Exception:
            try:
                self._record_msg(await update.message.reply_text(text))
            except Exception:
                self.logger.warning("Failed to deliver reply", exc_info=True)

    async def _maybe_alert_embed_failures(self, update: Update) -> None:
        """One-time heads-up when embeds have been failing in a row (dead token,
        endpoint down). Single blips stay silent — the text is saved and sitting
        in this chat, and the backfill sweeps it up. Checked after the turn, so no
        concurrent turn is mutating the failure counter."""
        if self.memory is None or not self.memory.take_failure_alert():
            return
        try:
            await update.message.reply_text(
                "⚠️ Heads up — I haven't been able to file the last few into "
                "semantic memory, so recall may be stale. Worth a look when you can."
            )
        except Exception:
            self.logger.warning("Failed to send embed-failure alert", exc_info=True)

    def _is_allowed(self, telegram_user_id: int) -> bool:
        """Only ids named in TELEGRAM_ALLOWED_USERS. An empty list admits
        nobody — an unconfigured bot must not be an open one."""
        return telegram_user_id in self.settings.telegram_allowed_users

    def _user(self, update: Update):
        # A private chat with an allowed person, or nothing. In a group the
        # reply — built from their private context — would be read by everyone.
        chat = update.effective_chat
        if chat is None or chat.type != "private":
            return None
        telegram_user_id = update.effective_user.id
        if not self._is_allowed(telegram_user_id):
            return None
        return self.database.ensure_user(
            telegram_user_id,
            str(self.settings.timezone),
        )


_TELEGRAM_LIMIT = 3900  # headroom under Telegram's hard 4096
_TELEGRAM_TIMEOUT = 30.0  # seconds; library default is 5


def _tg_len(text: str) -> int:
    """Telegram's 4096 cap counts UTF-16 code units (astral chars count as
    2), not Python code points — an emoji-dense reply near the limit would
    otherwise still exceed the wire cap and be lost."""
    return len(text.encode("utf-16-le")) // 2


def _chunk_message(text: str, limit: int = _TELEGRAM_LIMIT) -> list[str]:
    """Split at paragraph boundaries, hard-splitting any monster paragraph."""
    if _tg_len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        while _tg_len(para) > limit:        # a single over-limit paragraph
            if current:
                chunks.append(current)
                current = ""
            # limit//2 code points can never exceed `limit` UTF-16 units.
            head_len = limit if _tg_len(para[:limit]) <= limit else limit // 2
            chunks.append(para[:head_len])
            para = para[head_len:]
        candidate = f"{current}\n\n{para}" if current else para
        if _tg_len(candidate) > limit:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]
