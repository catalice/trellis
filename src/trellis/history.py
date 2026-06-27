from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any
from uuid import UUID

from trellis.postgres import PostgresDatabase


@dataclass(frozen=True)
class ConversationTurn:
    id: UUID
    user_id: UUID
    role: str
    content: str
    created_at: datetime


class PostgresConversationHistory:
    def __init__(self, database: PostgresDatabase, timezone: tzinfo | None = None):
        self.database = database
        self._timezone = timezone

    def append(self, user_id: UUID, role: str, content: str) -> None:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversation_turns (user_id, role, content)
                    VALUES (%s, %s, %s)
                    """,
                    (user_id, role, content),
                )

    def recent(self, user_id: UUID, limit: int = 12) -> list[ConversationTurn]:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, role, content, created_at
                    FROM conversation_turns
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = cur.fetchall()
        # Return in chronological order (oldest first) for the messages API.
        return [
            ConversationTurn(
                id=row[0],
                user_id=row[1],
                role=row[2],
                content=row[3],
                created_at=row[4],
            )
            for row in reversed(rows)
        ]

    def to_messages(self, turns: list[ConversationTurn]) -> list[dict[str, Any]]:
        while turns and turns[0].role == "assistant":
            turns = turns[1:]
        result = []
        for t in turns:
            ts = t.created_at
            if self._timezone is not None and ts.tzinfo is not None:
                ts = ts.astimezone(self._timezone)
            prefix = ts.strftime("[%a %d %b %H:%M] ")
            entry = {"role": t.role, "content": f"{prefix}{t.content}"}
            if result and result[-1]["role"] == t.role:
                result[-1]["content"] += f"\n{entry['content']}"
            else:
                result.append(entry)
        return result

    def prune(self, user_id: UUID, keep: int = 50) -> None:
        """Delete old turns, keeping the most recent `keep` per user."""
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM conversation_turns
                    WHERE user_id = %s
                      AND id NOT IN (
                        SELECT id FROM conversation_turns
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                      )
                    """,
                    (user_id, user_id, keep),
                )

    def domain_summary(self, user_id: UUID, domain: str) -> str | None:
        """Returns the stored conversation summary for this user+domain, or None."""
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT summary FROM conversation_summaries
                    WHERE user_id = %s AND domain = %s
                    """,
                    (user_id, domain),
                )
                row = cur.fetchone()
        return row[0] if row else None

    def save_domain_summary(
        self, user_id: UUID, domain: str, summary: str, turns_covered: int
    ) -> None:
        """Upserts the conversation summary for this user+domain."""
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversation_summaries (user_id, domain, summary, turns_covered, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (user_id, domain) DO UPDATE SET
                        summary = EXCLUDED.summary,
                        turns_covered = EXCLUDED.turns_covered,
                        updated_at = NOW()
                    """,
                    (user_id, domain, summary, turns_covered),
                )

    def turn_count(self, user_id: UUID) -> int:
        """Returns total number of turns stored for this user."""
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM conversation_turns WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
        return row[0] if row else 0
