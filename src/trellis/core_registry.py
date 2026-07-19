"""
The registry is the ONE place domains are wired into the oracle.

To add a domain:
  1. Write domain files (domain_models, domain_repo, domain_claude, domain_service, domain_tool)
  2. Call registry.add_domain() here — nothing else changes.

The oracle never imports a domain directly. It calls whatever is registered.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import UUID


# Type aliases
ContextLoader = Callable[[UUID, datetime], str | None]
ToolHandler = Callable[[UUID, dict, datetime], str]
ToolSchema = dict


@dataclass
class DomainRegistration:
    name: str
    context_loader: ContextLoader
    tools: list[tuple[ToolSchema, ToolHandler]]
    signals: list[str]          # keywords that trigger routing to this domain


class TrellisRegistry:
    def __init__(self) -> None:
        self._domains: dict[str, DomainRegistration] = {}

    def add_domain(
        self,
        name: str,
        context_loader: ContextLoader,
        tools: list[tuple[ToolSchema, ToolHandler]],
        signals: list[str],
    ) -> None:
        self._domains[name] = DomainRegistration(
            name=name,
            context_loader=context_loader,
            tools=tools,
            signals=signals,
        )

    # --- Oracle-facing interface -------------------------------------------
    # The oracle calls these. It never touches _domains directly.

    def all_signals(self) -> dict[str, list[str]]:
        """Returns {domain_name: [signals]} for the router."""
        return {name: d.signals for name, d in self._domains.items()}

    def load_context(self, domain: str, user_id: UUID, now: datetime) -> str | None:
        d = self._domains.get(domain)
        if d is None:
            return None
        return d.context_loader(user_id, now)

    def tools_for(self, domains: set[str]) -> list[tuple[ToolSchema, ToolHandler]]:
        """Returns tools for the given set of domain names."""
        result = []
        for name in domains:
            d = self._domains.get(name)
            if d:
                result.extend(d.tools)
        return result

    def domain_names(self) -> list[str]:
        return list(self._domains.keys())
