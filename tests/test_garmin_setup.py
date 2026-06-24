from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from trellis.config import Settings
from trellis.garmin_setup import (
    PostgresGarminConnectionRepository,
    _select_telegram_user,
)


def settings_with_allowed(*allowed: int) -> Settings:
    return Settings(
        telegram_bot_token="telegram-token",
        telegram_allowed_users=frozenset(allowed),
        anthropic_api_key="anthropic-key",
        anthropic_model="claude-test",
        database_url="postgresql://trellis:trellis@localhost:5433/trellis",
        obsidian_vault=Path("/tmp"),
        timezone=ZoneInfo("Europe/Madrid"),
        health_worker_url="http://health-worker:8001",
        health_worker_secret="worker-secret",
        trellis_secret_key="trellis-secret",
        lthr=None,
        max_hr=None,
    )


class GarminSetupTest(unittest.TestCase):
    def test_single_allowed_user_is_selected_without_prompt(self):
        with patch("builtins.input") as input_mock:
            selected = _select_telegram_user(settings_with_allowed(12345))

        self.assertEqual(12345, selected)
        input_mock.assert_not_called()

    def test_prompts_when_more_than_one_user_is_allowed(self):
        with patch("builtins.input", return_value="222"):
            selected = _select_telegram_user(settings_with_allowed(111, 222))

        self.assertEqual(222, selected)

    def test_repository_requires_secret_key(self):
        with self.assertRaisesRegex(ValueError, "secret key"):
            PostgresGarminConnectionRepository(object(), "")


if __name__ == "__main__":
    unittest.main()
