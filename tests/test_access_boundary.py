"""Trellis holds one person's private life. It answers that person, in a private
chat, and nobody else anywhere."""
from __future__ import annotations

from types import SimpleNamespace

from trellis.core_telegram import TelegramTrellis

OWNER = 1001


class _Db:
    def __init__(self):
        self.ensured = []

    def ensure_user(self, telegram_user_id, timezone):
        self.ensured.append(telegram_user_id)
        return f"user-{telegram_user_id}"


def _bot(allowed):
    bot = TelegramTrellis.__new__(TelegramTrellis)
    bot.settings = SimpleNamespace(telegram_allowed_users=frozenset(allowed), timezone="UTC")
    bot.database = _Db()
    return bot


def _update(sender, chat_type="private"):
    return SimpleNamespace(effective_user=SimpleNamespace(id=sender),
                           effective_chat=SimpleNamespace(type=chat_type))


def test_the_owner_in_a_private_chat_is_served():
    assert _bot({OWNER})._user(_update(OWNER)) == f"user-{OWNER}"


def test_the_owner_in_a_group_is_not_served():
    bot = _bot({OWNER})
    for chat_type in ("group", "supergroup", "channel"):
        assert bot._user(_update(OWNER, chat_type)) is None
    assert bot.database.ensured == []          # no context is even loaded


def test_a_stranger_is_not_served():
    assert _bot({OWNER})._user(_update(2002)) is None


def test_an_empty_allowlist_admits_nobody():
    bot = _bot(set())
    assert bot._user(_update(OWNER)) is None
    assert not bot._is_allowed(OWNER)


def test_compose_publishes_the_database_to_this_machine_only():
    from pathlib import Path
    compose = (Path(__file__).parent.parent / "docker-compose.yml").read_text()
    assert '"127.0.0.1:5433:5432"' in compose
    assert '- "5433:5432"' not in compose


def test_one_person_per_instance_is_enforced_at_startup(tmp_path):
    import dataclasses
    import pytest
    from trellis.core_config import Settings
    base = Settings.from_env()
    one = dataclasses.replace(base, telegram_bot_token="t", anthropic_api_key="k",
                              obsidian_vault=tmp_path, telegram_allowed_users=frozenset({1}))
    one.validate()
    with pytest.raises(ValueError, match="one person per instance"):
        dataclasses.replace(one, telegram_allowed_users=frozenset({1, 2})).validate()
