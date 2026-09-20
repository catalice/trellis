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
    one = dataclasses.replace(base, telegram_bot_token="t", anthropic_api_key="k", database_url="dsn",
                              obsidian_vault=tmp_path, telegram_allowed_users=frozenset({1}))
    one.validate()
    with pytest.raises(ValueError, match="one person per instance"):
        dataclasses.replace(one, telegram_allowed_users=frozenset({1, 2})).validate()


class TestDatabaseCredentials:
    """The install's own password, whatever characters it holds, and no public default."""

    def _settings(self, monkeypatch, **env):
        from trellis.core_config import Settings
        for key in ("DATABASE_URL", "POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_PORT"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr("trellis.core_config.load_dotenv", lambda *a, **k: None)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return Settings.from_env()

    def test_a_password_with_url_characters_connects_as_typed(self, monkeypatch):
        from psycopg2.extensions import parse_dsn
        awkward = "p@ss/w%rd with:space?&#"
        settings = self._settings(monkeypatch, POSTGRES_PASSWORD=awkward, POSTGRES_HOST="postgres", POSTGRES_PORT="5432")
        parsed = parse_dsn(settings.database_url)
        assert parsed["password"] == awkward
        assert (parsed["host"], parsed["port"], parsed["user"], parsed["dbname"]) == ("postgres", "5432", "trellis", "trellis")

    def test_an_explicit_database_url_still_wins(self, monkeypatch):
        settings = self._settings(monkeypatch, DATABASE_URL="postgresql://u:p@h:1/d", POSTGRES_PASSWORD="x")
        assert settings.database_url == "postgresql://u:p@h:1/d"

    def test_no_credentials_means_no_connection_string_and_a_clear_refusal(self, monkeypatch, tmp_path):
        import dataclasses
        import pytest
        settings = self._settings(monkeypatch)
        assert "trellis:trellis" not in settings.database_url
        ready = dataclasses.replace(settings, telegram_bot_token="t", anthropic_api_key="k",
                                    obsidian_vault=tmp_path, telegram_allowed_users=frozenset({1}))
        with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
            ready.validate()

    def test_the_repository_ships_no_default_database_password(self):
        from pathlib import Path
        root = Path(__file__).parent.parent
        for name in ("docker-compose.yml", ".env.example", "src/trellis/core_config.py", "scripts/backfill_embeddings.py"):
            assert "trellis:trellis@" not in (root / name).read_text(), name
        assert ":-trellis}" not in (root / "docker-compose.yml").read_text()
