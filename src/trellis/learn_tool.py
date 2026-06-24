from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from trellis.learn_models import LearningThread


LEARN_SIGNALS: list[str] = [
    "learn", "learning", "teach", "lesson", "bite",
    "thread", "threads", "map", "curriculum",
    "history", "science", "physics", "biology", "chemistry",
    "geopolitics", "politics", "geography", "anthropology",
    "neuroscience", "sociology", "culture", "civilisation", "civilization",
    "economics", "philosophy", "language", "evolution",
    "explain", "understand", "how did", "why did", "where did",
    "morning bite", "knowledge", "continue learning",
]


class _LearningService(Protocol):
    def create_thread(self, user_id: UUID, name: str, description: str | None) -> LearningThread: ...
    def list_threads(self, user_id: UUID) -> list[LearningThread]: ...
    def record_entry(self, user_id: UUID, thread_id: UUID, summary: str): ...
    def thread_state(self, user_id: UUID) -> list[dict]: ...
    def get_thread_history(self, user_id: UUID, name_reference: str, limit: int) -> dict | None: ...


START_THREAD_TOOL = {
    "name": "start_learning_thread",
    "description": (
        "Create a new learning thread — a named track on the user's knowledge map. "
        "Use when the user wants to start learning about a new area, or during onboarding "
        "when they tell you what they want to explore."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Short name for the thread, e.g. 'The world and how we got here'.",
            },
            "description": {
                "type": "string",
                "description": "Optional: scope or focus of this thread.",
            },
        },
        "required": ["name"],
    },
}

_RECORD_ENTRY_TOOL = {
    "name": "record_learning_entry",
    "description": (
        "Log what was just covered in a learning bite. Call this after delivering a bite "
        "so Trellis remembers where the thread is and doesn't repeat it. "
        "Keep the summary short — one or two sentences on the concept covered."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "thread_id": {
                "type": "string",
                "description": "UUID of the thread this bite belongs to.",
            },
            "summary": {
                "type": "string",
                "description": "Brief summary of what was covered — enough to avoid repeating it.",
            },
        },
        "required": ["thread_id", "summary"],
    },
}

_LIST_THREADS_TOOL = {
    "name": "list_learning_threads",
    "description": "List the user's active learning threads and what's been covered in each.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_GET_THREAD_HISTORY_TOOL = {
    "name": "get_thread_history",
    "description": (
        "Get the full history of a learning thread — every entry that's been recorded. "
        "Use when the user asks to review what they've covered on a topic, "
        "or wants to continue from a specific point."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "thread_name": {
                "type": "string",
                "description": "Name or substring of the learning thread to look up.",
            },
        },
        "required": ["thread_name"],
    },
}


def handle_start_learning_thread(user_id: UUID, input_dict: dict, now: datetime, *, learning_service) -> str:
    name = input_dict.get("name", "").strip()
    if not name:
        return "Thread name is required."
    description = input_dict.get("description") or None
    thread = learning_service.create_thread(user_id, name, description)
    return f"Thread started: '{thread.name}'."


def learn_tools(learning_service: _LearningService) -> list[tuple[dict, callable]]:
    def handle_start_thread(user_id: UUID, input_dict: dict, now: datetime) -> str:
        return handle_start_learning_thread(user_id, input_dict, now, learning_service=learning_service)

    def handle_record_entry(user_id: UUID, input_dict: dict, now: datetime) -> str:
        raw_id = input_dict.get("thread_id", "").strip()
        summary = input_dict.get("summary", "").strip()
        if not raw_id or not summary:
            return "thread_id and summary are required."
        try:
            thread_id = UUID(raw_id)
        except ValueError:
            return f"Invalid thread_id: {raw_id}"
        learning_service.record_entry(user_id, thread_id, summary)
        return "Entry recorded."

    def handle_list_threads(user_id: UUID, input_dict: dict, now: datetime) -> str:
        state = learning_service.thread_state(user_id)
        if not state:
            return "No learning threads yet. Start one to begin building your map."
        lines = []
        for item in state:
            thread = item["thread"]
            entries = item["recent_entries"]
            lines.append(f"**{thread.name}**")
            if thread.description:
                lines.append(f"  {thread.description}")
            if entries:
                lines.append(f"  Last covered: {entries[-1].summary}")
            else:
                lines.append("  Not started yet.")
        return "\n".join(lines)

    def handle_get_thread_history(user_id: UUID, input_dict: dict, now: datetime) -> str:
        thread_name = str(input_dict.get("thread_name", "")).strip()
        if not thread_name:
            return "Thread name is required."
        result = learning_service.get_thread_history(user_id, thread_name, limit=50)
        if result is None:
            return f"No active learning thread found matching '{thread_name}'."
        thread = result["thread"]
        entries = result["entries"]
        if not entries:
            return f"Thread '{thread.name}' exists but no entries recorded yet."
        lines = [f"Thread: {thread.name}", f"Entries ({len(entries)}):"]
        for e in entries:
            lines.append(f"  [{e.created_at.strftime('%d %b')}] {e.summary}")
        return "\n".join(lines)

    return [
        (START_THREAD_TOOL, handle_start_thread),
        (_RECORD_ENTRY_TOOL, handle_record_entry),
        (_LIST_THREADS_TOOL, handle_list_threads),
        (_GET_THREAD_HISTORY_TOOL, handle_get_thread_history),
    ]
