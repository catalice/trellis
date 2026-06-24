"""
TEMPLATE: domain_service.py
Copy to: src/trellis/{domain}_service.py

Rules:
- Thin orchestration only — validate input, call repo or claude, return str
- Never talks to Claude directly — delegates to domain_claude.py
- Never talks to the DB directly — delegates to domain_repo.py
- One method per action — no large if/elif chains
- On Claude failure: raise, don't return a degraded silent result
- Returns str always — the oracle formats it for the user
"""
from __future__ import annotations

from uuid import UUID

from trellis.domain_claude import ExampleClaude
from trellis.domain_models import ExampleRecord
from trellis.domain_repo import ExampleRepository


class ExampleService:
    def __init__(
        self,
        repository: ExampleRepository,
        claude: ExampleClaude,
    ) -> None:
        self.repository = repository
        self.claude = claude

    def get_summary(self, user_id: UUID) -> str:
        records = self.repository.list_for_user(user_id)
        if not records:
            return "No records found."
        context = "\n".join(r.summary() for r in records)
        result = self.claude.example_call(context)
        if result is None:
            raise RuntimeError("ExampleClaude unavailable")
        return result.result

    def save_record(self, user_id: UUID, value: str) -> str:
        from uuid import uuid4
        record = ExampleRecord(id=uuid4(), user_id=user_id, value=value)
        saved = self.repository.save(record)
        return f"Saved: {saved.summary()}"
