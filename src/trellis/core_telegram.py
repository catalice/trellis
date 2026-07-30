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
from trellis.domain_second_brain_service import ReminderService

# (audio_bytes) -> transcript
Transcriber = Callable[[bytes], str]

# NOTE: check-ins are NOT hardcoded here. When the user wants a morning/evening
# check-in, the oracle creates a real recurring reminder (set_reminder, recur_daily)
# — persisted, user-owned, editable, and surviving restarts. There is deliberately
# no baked-in ping schedule; that was removed because it wasn't tied to the user's
# choice and silently died on restart.


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
    ):
        self.settings = settings
        self.database = database
        self.assembler = assembler
        self.reminders = reminders
        self.transcriber = transcriber
        self.memory = memory
        self._reminder_delivery_task: asyncio.Task | None = None
        self.logger = logging.getLogger(__name__)

    def build(self) -> Application:
        application = (
            Application.builder()
            .token(self.settings.telegram_bot_token)
            .post_init(self._post_init)
            .post_shutdown(self._post_shutdown)
            .build()
        )
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.message)
        )
        application.add_handler(MessageHandler(filters.VOICE, self.voice))
        return application

    async def _post_init(self, application: Application) -> None:
        if self.reminders is None:
            return
        self._reminder_delivery_task = asyncio.create_task(
            self._deliver_due_reminders_loop(application)
        )

    async def _post_shutdown(self, application: Application) -> None:
        if self._reminder_delivery_task is None:
            return
        self._reminder_delivery_task.cancel()
        try:
            await self._reminder_delivery_task
        except asyncio.CancelledError:
            pass
        self._reminder_delivery_task = None

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
        for user_id, telegram_user_id in self.database.list_users():
            if (
                self.settings.telegram_allowed_users
                and telegram_user_id not in self.settings.telegram_allowed_users
            ):
                continue
            due = self.reminders.upcoming(user_id, hours=0, now=now)
            for reminder in due:
                await application.bot.send_message(
                    chat_id=telegram_user_id,
                    text=f"Reminder: {reminder.label}",
                )
                self.reminders.mark_sent(reminder.id)
                if reminder.recur_daily:
                    self.reminders.reschedule_daily(user_id, reminder, now=now)
                delivered += 1
        return delivered

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    async def _respond(self, update: Update, user_id, text: str) -> None:
        # A real "working" message, not just the typing indicator — sent now and
        # edited into the final reply, so there's visible feedback the whole turn.
        placeholder = await update.message.reply_text("🧠 on it…")
        try:
            reply = await asyncio.to_thread(
                self.assembler.handle_turn, user_id, text
            )
        except Exception:
            self.logger.exception("Oracle failed for user %s", user_id)
            reply = "Something went wrong. Nothing was changed — please try again."

        final = reply or "Something went wrong — no response was generated. Please try again."
        try:
            await placeholder.edit_text(final, parse_mode="Markdown")
        except Exception:
            # Edit can fail (reply too long for one message, a Markdown quirk) —
            # fall back to a fresh send so the answer always lands.
            self.logger.warning("Editing placeholder failed; sending fresh", exc_info=True)
            await update.message.reply_text(final, parse_mode="Markdown")

        await self._maybe_alert_embed_failures(update)

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

    def _user(self, update: Update):
        telegram_user_id = update.effective_user.id
        if (
            self.settings.telegram_allowed_users
            and telegram_user_id not in self.settings.telegram_allowed_users
        ):
            return None
        return self.database.ensure_user(
            telegram_user_id,
            str(self.settings.timezone),
        )
