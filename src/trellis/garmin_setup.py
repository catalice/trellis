from __future__ import annotations

import getpass
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from trellis.config import Settings
from trellis.garmin import GarminAuthStatus, GarminClient
from trellis.postgres import PostgresDatabase


@dataclass(frozen=True)
class GarminConnectionStatus:
    is_connected: bool
    sync_enabled: bool
    last_sync_at: object | None = None
    last_error: str | None = None


class PostgresGarminConnectionRepository:
    def __init__(self, database: PostgresDatabase, secret_key: str):
        if not secret_key.strip():
            raise ValueError("Trellis secret key is required")
        self.database = database
        self.secret_key = secret_key

    def save_connected(
        self,
        user_id: UUID,
        *,
        email: str,
        session_dump: str,
    ) -> None:
        if not email.strip():
            raise ValueError("Garmin email is required")
        if not session_dump.strip():
            raise ValueError("Garmin session dump is required")
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO garmin_connections (
                        user_id, email_encrypted, session_dump_encrypted,
                        is_connected, last_error
                    ) VALUES (
                        %s,
                        encode(pgp_sym_encrypt(%s, %s), 'base64'),
                        encode(pgp_sym_encrypt(%s, %s), 'base64'),
                        true,
                        NULL
                    )
                    ON CONFLICT (user_id) DO UPDATE SET
                        email_encrypted = EXCLUDED.email_encrypted,
                        session_dump_encrypted = EXCLUDED.session_dump_encrypted,
                        is_connected = true,
                        last_error = NULL,
                        updated_at = NOW()
                    """,
                    (
                        user_id,
                        email,
                        self.secret_key,
                        session_dump,
                        self.secret_key,
                    ),
                )

    def get_session_dump(self, user_id: UUID) -> str | None:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pgp_sym_decrypt(
                        decode(session_dump_encrypted, 'base64'),
                        %s
                    )
                    FROM garmin_connections
                    WHERE user_id = %s
                      AND is_connected = true
                      AND session_dump_encrypted IS NOT NULL
                    """,
                    (self.secret_key, user_id),
                )
                row = cursor.fetchone()
                return row[0] if row else None

    def status(self, user_id: UUID) -> GarminConnectionStatus:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT is_connected, sync_enabled, last_sync_at, last_error
                    FROM garmin_connections
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return GarminConnectionStatus(is_connected=False, sync_enabled=False)
        return GarminConnectionStatus(
            is_connected=row[0],
            sync_enabled=row[1],
            last_sync_at=row[2],
            last_error=row[3],
        )

    def mark_sync_success(self, user_id: UUID, synced_at: datetime) -> None:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE garmin_connections
                    SET last_sync_at = %s,
                        last_error = NULL,
                        updated_at = NOW()
                    WHERE user_id = %s
                    """,
                    (synced_at, user_id),
                )

    def get_last_sync_at(self, user_id: UUID) -> datetime | None:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT last_sync_at FROM garmin_connections WHERE user_id = %s",
                    (user_id,),
                )
                row = cursor.fetchone()
        return row[0] if row else None

    def mark_sync_failure(self, user_id: UUID, error: str) -> None:
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE garmin_connections
                    SET last_error = %s,
                        updated_at = NOW()
                    WHERE user_id = %s
                    """,
                    (error[:2000], user_id),
                )


def main() -> None:
    settings = Settings.from_env()
    settings.validate_health()

    database = PostgresDatabase(settings.database_url)
    database.migrate(Path(__file__).with_name("migrations"))

    telegram_user_id = _select_telegram_user(settings)
    user_id = database.ensure_user(telegram_user_id, str(settings.timezone))

    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")
    if not email or not password:
        raise SystemExit("Garmin email and password are required.")

    client = GarminClient(
        settings.health_worker_url,
        settings.health_worker_secret,
        timeout=90.0,
    )
    result = client.connect(email, password)
    password = ""

    if result.status is GarminAuthStatus.MFA_REQUIRED:
        code = input("Garmin MFA code: ").strip()
        result = client.complete_mfa(result.mfa_session_id or "", code)

    if result.status is not GarminAuthStatus.SUCCESS or not result.session_dump:
        raise SystemExit("Garmin did not return a usable session.")

    PostgresGarminConnectionRepository(
        database,
        settings.trellis_secret_key,
    ).save_connected(
        user_id,
        email=email,
        session_dump=result.session_dump,
    )
    print("Garmin connected. Session stored encrypted in PostgreSQL.")


def _select_telegram_user(settings: Settings) -> int:
    allowed = sorted(settings.telegram_allowed_users)
    if len(allowed) == 1:
        return allowed[0]
    if allowed:
        print("Known Telegram user IDs:")
        for value in allowed:
            print(f"- {value}")
    raw = input("Telegram user ID to attach Garmin to: ").strip()
    try:
        return int(raw)
    except ValueError as error:
        raise SystemExit("Telegram user ID must be an integer.") from error


if __name__ == "__main__":
    main()
