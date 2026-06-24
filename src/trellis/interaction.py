from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class InteractionResult:
    domain: str
    action: str
    changed_state: bool
    user_visible_response: str
    confidence: float | None = None
    facts: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ()
    safety_notes: tuple[str, ...] = ()
    raw: Mapping[str, str] = field(default_factory=dict)

    @property
    def state_change_text(self) -> str:
        return "yes" if self.changed_state else "no"
