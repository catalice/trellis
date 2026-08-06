"""
User profile + current context — who the user is (stable) and what's true right
now (life context that expires). Global Tier-1a services loaded every turn, plus
the user-preferences store. Models, services, and their Postgres repositories.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from psycopg2.extras import RealDictCursor

from trellis.infra_postgres import PostgresDatabase


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UserProfile:
    user_id: UUID
    name: str | None
    physical_notes: str | None
    cognitive_notes: str | None
    updated_at: datetime

    def is_empty(self) -> bool:
        return not self.name and not self.physical_notes and not self.cognitive_notes

    def for_coach(self) -> str:
        lines = []
        if self.name:
            lines.append(f"Name: {self.name}")
        if self.physical_notes:
            lines.append(f"Physical: {self.physical_notes}")
        if self.cognitive_notes:
            lines.append(f"Cognitive/exec: {self.cognitive_notes}")
        return "\n".join(lines)


@dataclass(frozen=True)
class CurrentContext:
    user_id: UUID
    physical_notes: str | None
    cognitive_notes: str | None
    misc_notes: str | None
    valid_until: date
    updated_at: datetime

    def is_valid(self, today: date) -> bool:
        return self.valid_until >= today

    def for_coach(self) -> str:
        lines = []
        if self.physical_notes:
            lines.append(f"Physical (current): {self.physical_notes}")
        if self.cognitive_notes:
            lines.append(f"Life/cognitive (current): {self.cognitive_notes}")
        if self.misc_notes:
            lines.append(f"Other (current): {self.misc_notes}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Repository protocols
# ---------------------------------------------------------------------------

class UserProfileRepository(Protocol):
    def get(self, user_id: UUID) -> UserProfile | None: ...
    def upsert(self, profile: UserProfile) -> UserProfile: ...


class CurrentContextRepository(Protocol):
    def get(self, user_id: UUID) -> CurrentContext | None: ...
    def upsert(self, context: CurrentContext) -> CurrentContext: ...


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

class UserProfileService:
    def __init__(self, repository: UserProfileRepository) -> None:
        self.repository = repository

    def get(self, user_id: UUID) -> UserProfile | None:
        return self.repository.get(user_id)

    def update(
        self,
        user_id: UUID,
        *,
        name: str | None = None,
        physical_notes: str | None = None,
        cognitive_notes: str | None = None,
    ) -> UserProfile:
        existing = self.repository.get(user_id)
        profile = UserProfile(
            user_id=user_id,
            name=name if name is not None else (existing.name if existing else None),
            physical_notes=physical_notes if physical_notes is not None
                           else (existing.physical_notes if existing else None),
            cognitive_notes=cognitive_notes if cognitive_notes is not None
                            else (existing.cognitive_notes if existing else None),
            updated_at=datetime.now(timezone.utc),
        )
        return self.repository.upsert(profile)


class CurrentContextService:
    def __init__(self, repository: CurrentContextRepository) -> None:
        self.repository = repository

    def get_valid(self, user_id: UUID, today: date) -> CurrentContext | None:
        ctx = self.repository.get(user_id)
        if ctx is None or not ctx.is_valid(today):
            return None
        return ctx

    def update(
        self,
        user_id: UUID,
        *,
        physical_notes: str | None = None,
        cognitive_notes: str | None = None,
        misc_notes: str | None = None,
        valid_days: int = 14,
        today: date,
    ) -> CurrentContext:
        existing = self.repository.get(user_id)
        ctx = CurrentContext(
            user_id=user_id,
            physical_notes=physical_notes if physical_notes is not None
                           else (existing.physical_notes if existing else None),
            cognitive_notes=cognitive_notes if cognitive_notes is not None
                            else (existing.cognitive_notes if existing else None),
            misc_notes=misc_notes if misc_notes is not None
                       else (existing.misc_notes if existing else None),
            valid_until=today + timedelta(days=valid_days),
            updated_at=datetime.now(timezone.utc),
        )
        return self.repository.upsert(ctx)


# ---------------------------------------------------------------------------
# Postgres repositories
# ---------------------------------------------------------------------------

class PostgresUserProfileRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get(self, user_id: UUID) -> UserProfile | None:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM user_profile WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
        return self._row(row) if row else None

    def upsert(self, profile: UserProfile) -> UserProfile:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO user_profile (user_id, name, physical_notes, cognitive_notes, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        physical_notes = EXCLUDED.physical_notes,
                        cognitive_notes = EXCLUDED.cognitive_notes,
                        updated_at = EXCLUDED.updated_at
                    RETURNING *
                    """,
                    (profile.user_id, profile.name, profile.physical_notes, profile.cognitive_notes, profile.updated_at),
                )
                return self._row(cur.fetchone())

    @staticmethod
    def _row(row: dict) -> UserProfile:
        return UserProfile(
            user_id=row["user_id"],
            name=row["name"],
            physical_notes=row["physical_notes"],
            cognitive_notes=row["cognitive_notes"],
            updated_at=row["updated_at"],
        )


class PostgresCurrentContextRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get(self, user_id: UUID) -> CurrentContext | None:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM current_context WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
        return self._row(row) if row else None

    def upsert(self, context: CurrentContext) -> CurrentContext:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO current_context
                        (user_id, physical_notes, cognitive_notes, misc_notes, valid_until, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        physical_notes = EXCLUDED.physical_notes,
                        cognitive_notes = EXCLUDED.cognitive_notes,
                        misc_notes = EXCLUDED.misc_notes,
                        valid_until = EXCLUDED.valid_until,
                        updated_at = EXCLUDED.updated_at
                    RETURNING *
                    """,
                    (
                        context.user_id, context.physical_notes, context.cognitive_notes,
                        context.misc_notes, context.valid_until, context.updated_at,
                    ),
                )
                return self._row(cur.fetchone())

    @staticmethod
    def _row(row: dict) -> CurrentContext:
        return CurrentContext(
            user_id=row["user_id"],
            physical_notes=row["physical_notes"],
            cognitive_notes=row["cognitive_notes"],
            misc_notes=row["misc_notes"],
            valid_until=row["valid_until"],
            updated_at=row["updated_at"],
        )


class PostgresPreferencesRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get(self, user_id: UUID, domain: str) -> str | None:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content FROM user_preferences WHERE user_id = %s AND domain = %s",
                    (user_id, domain),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def set(self, user_id: UUID, domain: str, content: str, *, replace: bool = False) -> None:
        """Save a preference. Preferences ACCUMULATE by default — a new one is
        appended to the domain's existing text, so saving 'keep replies short'
        can never silently erase 'no tables'. replace=True rewrites the whole
        domain deliberately (corrections, consolidation)."""
        if not replace:
            existing = self.get(user_id, domain)
            if existing:
                if content.strip() in existing:
                    content = existing
                else:
                    content = existing.rstrip() + "\n" + content.strip()
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_preferences (user_id, domain, content, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (user_id, domain) DO UPDATE
                        SET content = EXCLUDED.content, updated_at = NOW()
                    """,
                    (user_id, domain, content),
                )
