"""
Routes a message to relevant domains using keyword signals registered per domain.

Adding domain signals: call registry.add_domain(..., signals=[...]) in main.py.
The router never needs to change when domains change.

When no signal matches, the message routes to the default domain (the front
door — second_brain, passed in from main). A message can match multiple domains;
all matched domains load their context and tools.
"""
from __future__ import annotations


class Router:
    def __init__(
        self,
        domain_signals: dict[str, list[str]],
        default_domain: str | None = None,
    ) -> None:
        # {domain_name: [lowercase keywords]}
        self._signals = {
            domain: [s.lower() for s in signals]
            for domain, signals in domain_signals.items()
        }
        self._default = default_domain

    def route(self, message: str) -> set[str]:
        text = message.lower()
        matched = {
            domain
            for domain, keywords in self._signals.items()
            if any(kw in text for kw in keywords)
        }
        if not matched and self._default:
            matched.add(self._default)
        return matched
