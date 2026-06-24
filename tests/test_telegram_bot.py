from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from trellis.telegram_bot import TelegramTrellis


TZ = ZoneInfo("Europe/Madrid")


def _make_settings(**kwargs):
    settings = MagicMock()
    settings.telegram_bot_token = "test-token"
    settings.telegram_allowed_users = set()
    settings.timezone = TZ
    for key, value in kwargs.items():
        setattr(settings, key, value)
    return settings


def _make_database(user_id=None):
    db = MagicMock()
    db.ensure_user.return_value = user_id or uuid4()
    db.list_users.return_value = []
    return db


def _make_assembler(reply="ok"):
    assembler = MagicMock()
    assembler.handle_turn.return_value = reply
    return assembler


def _make_update(text="hello", telegram_user_id=12345):
    update = MagicMock()
    update.effective_user.id = telegram_user_id
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    return update


# ---------------------------------------------------------------------------
# _user — auth
# ---------------------------------------------------------------------------

def test_user_allowed_when_allowlist_empty():
    db = _make_database()
    bot = TelegramTrellis(_make_settings(), db, _make_assembler())
    update = _make_update(telegram_user_id=99)
    result = bot._user(update)
    assert result is not None
    db.ensure_user.assert_called_once()


def test_user_blocked_when_not_in_allowlist():
    db = _make_database()
    settings = _make_settings(telegram_allowed_users={111, 222})
    bot = TelegramTrellis(settings, db, _make_assembler())
    update = _make_update(telegram_user_id=999)
    result = bot._user(update)
    assert result is None
    db.ensure_user.assert_not_called()


def test_user_allowed_when_in_allowlist():
    db = _make_database()
    settings = _make_settings(telegram_allowed_users={111, 222})
    bot = TelegramTrellis(settings, db, _make_assembler())
    update = _make_update(telegram_user_id=111)
    result = bot._user(update)
    assert result is not None


# ---------------------------------------------------------------------------
# message handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_message_calls_oracle_and_replies():
    user_id = uuid4()
    assembler = _make_assembler(reply="Here is your answer.")
    db = _make_database(user_id=user_id)
    bot = TelegramTrellis(_make_settings(), db, assembler)
    update = _make_update(text="What's my plan today?")

    await bot.message(update, MagicMock())

    assembler.handle_turn.assert_called_once()
    call_args = assembler.handle_turn.call_args
    assert call_args[0][0] == user_id
    assert call_args[0][1] == "What's my plan today?"
    update.message.reply_text.assert_called_once_with("Here is your answer.", parse_mode="Markdown")


@pytest.mark.asyncio
async def test_message_blocked_user_gets_no_reply():
    settings = _make_settings(telegram_allowed_users={111})
    assembler = _make_assembler()
    db = _make_database()
    bot = TelegramTrellis(settings, db, assembler)
    update = _make_update(telegram_user_id=999)

    await bot.message(update, MagicMock())

    assembler.handle_turn.assert_not_called()
    update.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_message_oracle_error_returns_safe_reply():
    assembler = MagicMock()
    assembler.handle_turn.side_effect = RuntimeError("API down")
    db = _make_database()
    bot = TelegramTrellis(_make_settings(), db, assembler)
    update = _make_update()

    await bot.message(update, MagicMock())

    reply_text = update.message.reply_text.call_args[0][0]
    assert "Something went wrong" in reply_text
    assert "Nothing was changed" in reply_text


@pytest.mark.asyncio
async def test_message_sends_typing_action():
    bot = TelegramTrellis(_make_settings(), _make_database(), _make_assembler())
    update = _make_update()

    await bot.message(update, MagicMock())

    update.message.chat.send_action.assert_called_once_with("typing")


# ---------------------------------------------------------------------------
# start handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_replies():
    bot = TelegramTrellis(_make_settings(), _make_database(), _make_assembler())
    update = _make_update()
    update.message.reply_text = AsyncMock()

    await bot.start(update, MagicMock())

    update.message.reply_text.assert_called_once()
    text = update.message.reply_text.call_args[0][0]
    assert "Trellis" in text
