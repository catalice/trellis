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
    signals: list[str]          # keywords — the fallback router when embeddings are down
    rooms: list[str] | None = None  # the rooms inside this house, embedded for semantic routing


class TrellisRegistry:
    def __init__(self) -> None:
        self._domains: dict[str, DomainRegistration] = {}

    def add_domain(
        self,
        name: str,
        context_loader: ContextLoader,
        tools: list[tuple[ToolSchema, ToolHandler]],
        signals: list[str],
        rooms: list[str] | None = None,
    ) -> None:
        self._domains[name] = DomainRegistration(
            name=name,
            context_loader=context_loader,
            tools=tools,
            signals=signals,
            rooms=rooms,
        )

    # --- Oracle-facing interface -------------------------------------------
    # The oracle calls these. It never touches _domains directly.

    def all_signals(self) -> dict[str, list[str]]:
        """Returns {domain_name: [signals]} for the keyword (fallback) router."""
        return {name: d.signals for name, d in self._domains.items()}

    def all_rooms(self) -> dict[str, list[str]]:
        """Returns {domain_name: rooms} for the semantic router — only domains
        that declared rooms (no rooms -> keyword-routed only)."""
        return {name: d.rooms for name, d in self._domains.items() if d.rooms}

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

    def all_tools(self) -> list[tuple[ToolSchema, ToolHandler]]:
        """Every registered domain's tools. Tool AVAILABILITY is never gated by
        keyword routing — the model always sees every tool and decides what to call
        from the tool descriptions. (Keyword routing still shapes CONTEXT only.)
        This prevents the model from denying a capability just because a message
        didn't happen to match a domain's keywords."""
        result: list[tuple[ToolSchema, ToolHandler]] = []
        for d in self._domains.values():
            result.extend(d.tools)
        return result

    def domain_names(self) -> list[str]:
        return list(self._domains.keys())
