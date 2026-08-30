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
    def search(self, query: str, *, max_results: int = 5, source: str = "web") -> SearchResponse | None: ...


class SearchGateway:
    """One door to the outside world, many sources behind it: web + news
    (Tavily, plus the Guardian when a key is configured), pubmed (NCBI),
    scholar (OpenAlex), trials (ClinicalTrials.gov). The keyless sources work
    even with no Tavily key — a fresh install can cite papers on day one."""

    def __init__(self, api_key: str, guardian_key: str = "") -> None:
        self._api_key = api_key
        self._guardian_key = guardian_key

    def search(self, query: str, *, max_results: int = 5, source: str = "web") -> SearchResponse | None:
        if source == "pubmed":
            return pubmed_search(query, max_results=max_results)
        if source == "scholar":
            return openalex_search(query, max_results=max_results)
        if source == "trials":
            return clinical_trials_search(query, max_results=max_results)
        if source == "news" and self._guardian_key:
            guardian = guardian_search(query, self._guardian_key, max_results=max_results)
            if guardian and len(guardian.results) >= max_results:
                return guardian
            tavily = self._tavily(query, max_results=max_results, source="news")
            if guardian and tavily:
                seen = {r.url for r in guardian.results}
                merged = guardian.results + tuple(
                    r for r in tavily.results if r.url not in seen)
                return SearchResponse(query=query, answer=tavily.answer,
                                      results=merged[:max_results * 2])
            return guardian or tavily
        return self._tavily(query, max_results=max_results, source=source)

    def _tavily(self, query: str, *, max_results: int, source: str) -> SearchResponse | None:
        if not self._api_key:
            return None
        try:
            body = {
                "query": query[:400],
                "max_results": max(1, min(10, max_results)),
                "include_answer": True,
                "search_depth": "basic",
            }
            if source == "news":
                body["topic"] = "news"
            response = httpx.post(
                _TAVILY_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
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


# ---------------------------------------------------------------------------
# PubMed — peer-reviewed citations via NCBI E-utilities (free, no key).
# Source-in-truth's best friend: every result is a real, linkable paper.
# ---------------------------------------------------------------------------

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def pubmed_search(query: str, *, max_results: int = 5) -> SearchResponse | None:
    try:
        ids_resp = httpx.get(
            f"{_EUTILS}/esearch.fcgi",
            params={
                "db": "pubmed", "term": query[:300], "retmode": "json",
                "retmax": max(1, min(10, max_results)), "sort": "relevance",
            },
            timeout=_TIMEOUT,
        )
        ids_resp.raise_for_status()
        ids = ids_resp.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return None
        summary_resp = httpx.get(
            f"{_EUTILS}/esummary.fcgi",
            params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
            timeout=_TIMEOUT,
        )
        summary_resp.raise_for_status()
        docs = summary_resp.json().get("result", {})
    except Exception:
        _log.warning("pubmed_search failed for %r", query[:60], exc_info=True)
        return None

    results = []
    for pmid in ids:
        doc = docs.get(pmid) or {}
        if not doc.get("title"):
            continue
        authors = ", ".join(
            a.get("name", "") for a in (doc.get("authors") or [])[:3] if a.get("name")
        )
        bits = [b for b in (
            doc.get("fulljournalname") or doc.get("source"),
            doc.get("pubdate"), authors,
        ) if b]
        results.append(SearchResult(
            title=str(doc["title"]).strip(),
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            snippet=" · ".join(bits),
        ))
    if not results:
        return None
    return SearchResponse(query=query, answer=None, results=tuple(results))


def guardian_search(query: str, api_key: str, *, max_results: int = 5) -> SearchResponse | None:
    """The Guardian Open Platform — quality journalism, structured, citable."""
    try:
        resp = httpx.get(
            "https://content.guardianapis.com/search",
            params={"q": query[:300], "api-key": api_key,
                    "page-size": max(1, min(10, max_results)),
                    "show-fields": "trailText"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        items = (resp.json().get("response") or {}).get("results") or []
    except Exception:
        _log.warning("guardian_search failed for %r", query[:60], exc_info=True)
        return None
    results = tuple(
        SearchResult(
            title=str(i.get("webTitle", "")).strip(),
            url=str(i.get("webUrl", "")).strip(),
            snippet=" · ".join(b for b in (
                i.get("sectionName"),
                str(i.get("webPublicationDate", ""))[:10],
                str((i.get("fields") or {}).get("trailText", "")).strip()[:160],
            ) if b),
        )
        for i in items if i.get("webUrl")
    )
    return SearchResponse(query=query, answer=None, results=results) if results else None


def openalex_search(query: str, *, max_results: int = 5) -> SearchResponse | None:
    """OpenAlex — scholarly works across every field, free, keyless."""
    try:
        resp = httpx.get(
            "https://api.openalex.org/works",
            params={"search": query[:300], "per-page": max(1, min(10, max_results))},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        works = resp.json().get("results") or []
    except Exception:
        _log.warning("openalex_search failed for %r", query[:60], exc_info=True)
        return None
    results = []
    for w in works:
        title = str(w.get("display_name") or "").strip()
        if not title:
            continue
        url = w.get("doi") or w.get("id") or ""
        venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
        authors = ", ".join(
            (a.get("author") or {}).get("display_name", "")
            for a in (w.get("authorships") or [])[:3]
        ).strip(", ")
        bits = [b for b in (venue, str(w.get("publication_year") or ""), authors) if b]
        results.append(SearchResult(title=title, url=str(url), snippet=" · ".join(bits)))
    return SearchResponse(query=query, answer=None, results=tuple(results)) if results else None


def clinical_trials_search(query: str, *, max_results: int = 5) -> SearchResponse | None:
    """ClinicalTrials.gov v2 — registered trials, free, keyless."""
    try:
        resp = httpx.get(
            "https://clinicaltrials.gov/api/v2/studies",
            params={"query.term": query[:300], "pageSize": max(1, min(10, max_results))},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        studies = resp.json().get("studies") or []
    except Exception:
        _log.warning("clinical_trials_search failed for %r", query[:60], exc_info=True)
        return None
    results = []
    for s in studies:
        proto = s.get("protocolSection") or {}
        ident = proto.get("identificationModule") or {}
        nct = ident.get("nctId")
        title = str(ident.get("briefTitle") or "").strip()
        if not (nct and title):
            continue
        status = (proto.get("statusModule") or {}).get("overallStatus", "")
        conditions = ", ".join((proto.get("conditionsModule") or {}).get("conditions") or [])
        results.append(SearchResult(
            title=title,
            url=f"https://clinicaltrials.gov/study/{nct}",
            snippet=" · ".join(b for b in (status, conditions[:120]) if b),
        ))
    return SearchResponse(query=query, answer=None, results=tuple(results)) if results else None
