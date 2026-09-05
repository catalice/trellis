from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any
from uuid import UUID

from trellis.infra_postgres import PostgresDatabase

_log = logging.getLogger(__name__)


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

    def append(self, user_id: UUID, role: str, content: str, metadata: dict | None = None) -> None:
        import json
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversation_turns (user_id, role, content, metadata)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (user_id, role, content, json.dumps(metadata or {})),
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

    def recent_window(self, user_id: UUID, *, since, cap: int = 60) -> list[ConversationTurn]:
        """Verbatim memory is TIME-based (her design): everything since `since`,
        capped so a wild day can't run away. Newest-first query, returned oldest-first."""
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, role, content, created_at
                    FROM conversation_turns
                    WHERE user_id = %s AND created_at >= %s
                    ORDER BY created_at DESC LIMIT %s
                    """,
                    (user_id, since, cap),
                )
                rows = cur.fetchall()
        return [
            ConversationTurn(
                id=row[0], user_id=row[1], role=row[2],
                content=row[3], created_at=row[4],
            )
            for row in reversed(rows)
        ]

    # --- Telegram message registry (chat sweep) ----------------------------

    def record_telegram_message(self, chat_id: int, message_id: int) -> None:
        try:
            with self.database.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO telegram_messages (chat_id, message_id)"
                        " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (chat_id, message_id),
                    )
        except Exception:
            _log.warning("telegram message record failed", exc_info=True)

    def sweepable_telegram_messages(self, *, older_than) -> list[tuple[int, int, Any]]:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT chat_id, message_id, sent_at FROM telegram_messages"
                    " WHERE sent_at < %s ORDER BY sent_at",
                    (older_than,),
                )
                return list(cur.fetchall())

    def get_marker(self, chat_id: int) -> int | None:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT message_id FROM chat_markers WHERE chat_id = %s", (chat_id,))
                row = cur.fetchone()
                return row[0] if row else None

    def set_marker(self, chat_id: int, message_id: int) -> None:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_markers (chat_id, message_id, sent_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (chat_id) DO UPDATE
                        SET message_id = EXCLUDED.message_id, sent_at = NOW()
                    """,
                    (chat_id, message_id),
                )

    def forget_telegram_message(self, chat_id: int, message_id: int) -> None:
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM telegram_messages WHERE chat_id = %s AND message_id = %s",
                    (chat_id, message_id),
                )

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

    def max_turns_covered(self, user_id: UUID) -> int:
        """The turn_count recorded at the most recent summarisation, across all
        domains — the summariser's cursor. Lives in the DB, not process memory,
        so a restart doesn't re-trigger summarisation (0 if never summarised)."""
        with self.database.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(turns_covered), 0) FROM conversation_summaries"
                    " WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
        return row[0] if row else 0

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
