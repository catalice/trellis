"""
Web search — Trellis's window onto the outside world (read-only).

Swappable by design: everything depends on the WebSearch protocol, so adding
another provider (Brave, Allerac, Claude-native) is one new class here plus a
one-line change in core_main. Nothing else in Trellis knows or cares who searches.

Current implementation: Tavily (https://tavily.com) — an AI-native search API.
Free tier ~1000 searches/month; each basic search is 1 credit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

_log = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_TIMEOUT = 20.0


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class SearchResponse:
    query: str
    answer: str | None                      # provider's synthesised answer, if any
    results: tuple[SearchResult, ...]


class WebSearch(Protocol):
    def search(self, query: str, *, max_results: int = 5) -> SearchResponse | None: ...


class TavilySearch:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def search(self, query: str, *, max_results: int = 5) -> SearchResponse | None:
        if not self._api_key:
            return None
        try:
            response = httpx.post(
                _TAVILY_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "query": query[:400],
                    "max_results": max(1, min(10, max_results)),
                    "include_answer": True,
                    "search_depth": "basic",
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            _log.warning("TavilySearch failed for %r", query[:60], exc_info=True)
            return None

        results = tuple(
            SearchResult(
                title=str(r.get("title", "")).strip(),
                url=str(r.get("url", "")).strip(),
                snippet=str(r.get("content", "")).strip(),
            )
            for r in data.get("results", [])
            if r.get("url")
        )
        answer = str(data.get("answer") or "").strip() or None
        if not results and not answer:
            return None
        return SearchResponse(query=query, answer=answer, results=results)
