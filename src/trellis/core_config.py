from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_allowed_users: frozenset[int]
    anthropic_api_key: str
    anthropic_model: str
    database_url: str
    obsidian_vault: Path
    timezone: ZoneInfo
    health_worker_url: str
    health_worker_secret: str
    trellis_secret_key: str
    lthr: int | None
    max_hr: int | None
    groq_api_key: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        allowed = frozenset(
            int(value.strip())
            for value in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",")
            if value.strip()
        )
        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_allowed_users=allowed,
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            anthropic_model=os.getenv(
                "ANTHROPIC_MODEL",
                "claude-sonnet-4-6",
            ),
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql://trellis:trellis@localhost:5433/trellis",
            ),
            obsidian_vault=Path(
                os.getenv("OBSIDIAN_VAULT", "/Users/catalice/Documents/Obsidian")
            ).expanduser(),
            timezone=ZoneInfo(os.getenv("TRELLIS_TIMEZONE", "Europe/Madrid")),
            health_worker_url=os.getenv(
                "HEALTH_WORKER_URL",
                "http://health-worker:8001",
            ),
            health_worker_secret=os.getenv("HEALTH_WORKER_SECRET", ""),
            trellis_secret_key=os.getenv("TRELLIS_SECRET_KEY", ""),
            lthr=_int_env("TRELLIS_LTHR"),
            max_hr=_int_env("TRELLIS_MAX_HR"),
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
        )

    def validate(self) -> None:
        if not self.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        if not self.obsidian_vault.is_dir():
            raise ValueError(f"Obsidian vault does not exist: {self.obsidian_vault}")

    def validate_health(self) -> None:
        if not self.health_worker_url.strip():
            raise ValueError("HEALTH_WORKER_URL is required for Garmin sync")
        if not self.health_worker_secret.strip():
            raise ValueError("HEALTH_WORKER_SECRET is required for Garmin sync")
        if not self.trellis_secret_key.strip():
            raise ValueError("TRELLIS_SECRET_KEY is required for Garmin sync")


def _int_env(key: str) -> int | None:
    val = os.getenv(key, "").strip()
    return int(val) if val else None
