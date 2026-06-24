"""
Routes a message to relevant domains using keyword signals registered per domain.

Adding domain signals: call registry.add_domain(..., signals=[...]) in main.py.
The router never needs to change when domains change.
"""
from __future__ import annotations

_SHORT_MESSAGE_WORDS = 4


class Router:
    def __init__(self, domain_signals: dict[str, list[str]]) -> None:
        # {domain_name: [lowercase keywords]}
        self._signals = {
            domain: [s.lower() for s in signals]
            for domain, signals in domain_signals.items()
        }

    def route(self, message: str) -> set[str]:
        text = message.lower()
        matched = {
            domain
            for domain, keywords in self._signals.items()
            if any(kw in text for kw in keywords)
        }
        if not matched or len(text.split()) <= _SHORT_MESSAGE_WORDS:
            matched.add("meta")
        return matched
