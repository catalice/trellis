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

    def get(self, user_id: UUID) -> CurrentContext | None:
        """The stored context even if EXPIRED — the Brain page shows truth."""
        return self.repository.get(user_id)

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
    """Preferences as ROWS (migration 021): one rule, one row, one id —
    listable, updatable, removable. get() composes the joined text the
    assembler has always read."""

    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get(self, user_id: UUID, domain: str) -> str | None:
        rules = self.list_rules(user_id, domain)
        return "\n".join(r["rule"] for r in rules) or None

    def list_rules(self, user_id: UUID, domain: str | None = None) -> list[dict]:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                if domain is None:
                    cur.execute(
                        "SELECT id, domain, rule FROM preference_rules"
                        " WHERE user_id = %s ORDER BY domain, created_at",
                        (user_id,),
                    )
                else:
                    cur.execute(
                        "SELECT id, domain, rule FROM preference_rules"
                        " WHERE user_id = %s AND domain = %s ORDER BY created_at",
                        (user_id, domain),
                    )
                return [{"id": r[0], "domain": r[1], "rule": r[2]} for r in cur.fetchall()]

    def add_rule(self, user_id: UUID, domain: str, rule: str) -> None:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM preference_rules WHERE user_id = %s AND domain = %s"
                    " AND lower(rule) = lower(%s)",
                    (user_id, domain, rule.strip()),
                )
                if cur.fetchone():
                    return
                cur.execute(
                    "INSERT INTO preference_rules (user_id, domain, rule) VALUES (%s, %s, %s)",
                    (user_id, domain, rule.strip()),
                )

    def update_rule(self, user_id: UUID, rule_id: UUID, rule: str) -> bool:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE preference_rules SET rule = %s, updated_at = NOW()"
                    " WHERE id = %s AND user_id = %s",
                    (rule.strip(), rule_id, user_id),
                )
                return cur.rowcount > 0

    def remove_rule(self, user_id: UUID, rule_id: UUID) -> bool:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM preference_rules WHERE id = %s AND user_id = %s",
                    (rule_id, user_id),
                )
                return cur.rowcount > 0
