"""
User profile + current context — who the user is (stable) and what's true right
now (life context that expires). Global Tier-1a services loaded every turn, plus
the user-preferences store. Models, services, and their Postgres repositories.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID, uuid4

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
class ContextEntry:
    """One line of life context: their words, the day they said it, when it lapses."""
    id: UUID
    user_id: UUID
    text: str
    said_on: date
    expires_on: date


WORD_LIMIT = 10          # one line, not waffle — context entries and preference rules alike
CONTEXT_CAP = 12         # live context entries; the slot loads every turn
CONTEXT_DAYS = 14
_SIMILAR = 0.78         # bge-small: real repeats ~0.80+, different rules ≤0.67; a false warning is cheap


class TooLong(ValueError):
    def __init__(self, words: int, limit: int) -> None:
        super().__init__(f"{words} words, limit {limit}")
        self.words, self.limit = words, limit


class AtCap(ValueError):
    def __init__(self, entries: list) -> None:
        super().__init__("context is full")
        self.entries = entries


class LineGuard:
    """Shape control for anything the model writes that loads every turn. Python
    can't judge wording; it can refuse length and notice a near-repeat."""

    def __init__(self, embedder=None, *, limit: int = WORD_LIMIT, reference: list[str] | None = None) -> None:
        self._embedder = embedder
        self.limit = limit
        self._reference = list(reference or [])

    def check_length(self, text: str) -> None:
        words = len(text.split())
        if words > self.limit:
            raise TooLong(words, self.limit)

    def similar(self, text: str, others: list[str]) -> str | None:
        """The closest existing line if it's near enough to be the same rule,
        else None. No embedder, or a failed embed, means no warning — never a block."""
        pool = [o for o in [*others, *self._reference] if o and o != text]
        if self._embedder is None or not pool:
            return None
        try:
            vectors = self._embedder.embed([text, *pool])
        except Exception:
            return None
        if not vectors:
            return None
        head, rest = vectors[0], vectors[1:]
        def cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(y * y for y in b) ** 0.5
            return dot / (na * nb) if na and nb else 0.0
        score, line = max(((cos(head, v), o) for v, o in zip(rest, pool)), key=lambda s: s[0])
        return line if score >= _SIMILAR else None


# ---------------------------------------------------------------------------
# Repository protocols
# ---------------------------------------------------------------------------

class UserProfileRepository(Protocol):
    def get(self, user_id: UUID) -> UserProfile | None: ...
    def upsert(self, profile: UserProfile) -> UserProfile: ...


class CurrentContextRepository(Protocol):
    def live(self, user_id: UUID, today: date) -> list[ContextEntry]: ...
    def add(self, entry: ContextEntry) -> ContextEntry: ...
    def remove(self, user_id: UUID, entry_id: UUID) -> bool: ...


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
    def __init__(self, repository: CurrentContextRepository, guard: LineGuard | None = None) -> None:
        self.repository = repository
        self.guard = guard or LineGuard()

    def live(self, user_id: UUID, today: date) -> list[ContextEntry]:
        """Unexpired entries, newest first."""
        return self.repository.live(user_id, today)

    def add(
        self, user_id: UUID, text: str, *, today: date, until: date | None = None,
    ) -> tuple[ContextEntry, str | None]:
        """Returns the saved entry and the near-duplicate line it resembles, if any.
        Raises TooLong / AtCap — refused, never trimmed."""
        text = " ".join(text.split())
        self.guard.check_length(text)
        current = self.repository.live(user_id, today)
        if len(current) >= CONTEXT_CAP:
            raise AtCap(current)
        similar = self.guard.similar(text, [e.text for e in current])
        entry = self.repository.add(ContextEntry(
            id=uuid4(), user_id=user_id, text=text, said_on=today,
            expires_on=until or today + timedelta(days=CONTEXT_DAYS),
        ))
        return entry, similar

    def remove(self, user_id: UUID, text: str, *, today: date) -> ContextEntry | None:
        """Remove the one live entry these words point at; None when they match
        nothing or more than one."""
        needle = " ".join(text.lower().split())
        hits = [e for e in self.repository.live(user_id, today) if needle and needle in e.text.lower()]
        exact = [e for e in hits if e.text.lower() == needle]
        chosen = exact or hits
        if len(chosen) != 1:
            return None
        return chosen[0] if self.repository.remove(user_id, chosen[0].id) else None


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

    def live(self, user_id: UUID, today: date) -> list[ContextEntry]:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM context_entries
                    WHERE user_id = %s AND expires_on >= %s
                    ORDER BY said_on DESC, created_at DESC
                    """,
                    (user_id, today),
                )
                return [self._row(r) for r in cur.fetchall()]

    def add(self, entry: ContextEntry) -> ContextEntry:
        with self.database.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO context_entries (id, user_id, text, said_on, expires_on)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (entry.id, entry.user_id, entry.text, entry.said_on, entry.expires_on),
                )
                return self._row(cur.fetchone())

    def remove(self, user_id: UUID, entry_id: UUID) -> bool:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM context_entries WHERE user_id = %s AND id = %s",
                    (user_id, entry_id),
                )
                return cur.rowcount > 0

    @staticmethod
    def _row(row: dict) -> ContextEntry:
        return ContextEntry(
            id=row["id"], user_id=row["user_id"], text=row["text"],
            said_on=row["said_on"], expires_on=row["expires_on"],
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
