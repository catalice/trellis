from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from trellis.assembler import Assembler
from trellis.config import Settings
from trellis.garmin_sync import GarminSyncService
from trellis.postgres import PostgresDatabase
from trellis.reminders import ReminderService


class TelegramTrellis:
    def __init__(
        self,
        settings: Settings,
        database: PostgresDatabase,
        assembler: Assembler,
        reminders: ReminderService | None = None,
        garmin_sync_service: GarminSyncService | None = None,
        pattern_engine=None,
    ):
        self.settings = settings
        self.database = database
        self.assembler = assembler
        self.reminders = reminders
        self.garmin_sync_service = garmin_sync_service
        self.pattern_engine = pattern_engine
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
        if self.pattern_engine is not None:
            if application.job_queue is None:
                self.logger.warning("Scheduled jobs disabled: job-queue extra not installed")
            else:
                application.job_queue.run_daily(
                    self._run_pattern_scan,
                    time=time(4, 0, tzinfo=self.settings.timezone),
                    name="pattern_scan",
                )
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
            due = [
                reminder
                for reminder in self.reminders.list_scheduled(user_id)
                if reminder.remind_at.astimezone(timezone.utc) <= now
            ]
            for reminder in due:
                await application.bot.send_message(
                    chat_id=telegram_user_id,
                    text=f"Reminder: {reminder.task_title}",
                )
                self.reminders.mark_sent(reminder.id)
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

        text = update.message.text
        now = datetime.now(self.settings.timezone)

        await update.message.chat.send_action("typing")
        try:
            reply = await asyncio.to_thread(
                self.assembler.handle_turn, user_id, text
            )
        except Exception:
            self.logger.exception("Oracle failed for user %s", user_id)
            reply = "Something went wrong. Nothing was changed — please try again."

        await update.message.reply_text(reply, parse_mode="Markdown")

    async def _run_pattern_scan(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.pattern_engine is None or not self.settings.telegram_allowed_users:
            return
        telegram_id = next(iter(self.settings.telegram_allowed_users))
        now = datetime.now(self.settings.timezone)
        today = now.date()
        user_id = self.database.ensure_user(telegram_id, str(self.settings.timezone))
        try:
            await asyncio.to_thread(self.pattern_engine.run, user_id, today)
            self.logger.info("Pattern scan completed for user %s", user_id)
        except Exception:
            self.logger.exception("Pattern scan failed for user %s", user_id)

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
