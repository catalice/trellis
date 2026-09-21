"""
Web search — Trellis's window onto the outside world (read-only).

Swappable by design: everything depends on the WebSearch protocol, so adding
another provider (Brave, Allerac, Claude-native) is one new class here plus a
one-line change in core_main. Nothing else in Trellis knows or cares who searches.

Current implementation: Tavily (https://tavily.com) — an AI-native search API.
Free tier ~1000 searches/month; each basic search is 1 credit.

Two different things come back, and they are never mixed up:
  search — a LISTING: titles, links, fragments. Enough to choose a source, never
           enough to explain from. The provider's synthesised answer is not
           requested: it is another model's recall, not a source.
  read   — the SOURCE'S OWN TEXT, labelled with how much of it was reached
           (full text, abstract only, registry record, page text).
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Protocol

import httpx

_log = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
_TIMEOUT = 20.0


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class SearchResponse:
    query: str
    results: tuple[SearchResult, ...]


# How much of a source was actually reached. Said out loud with the text, and
# kept with a saved reference: an abstract is not the paper.
FULL_TEXT = "full text"
ABSTRACT = "abstract only"
REGISTRY = "registry record"
PAGE = "page text"


@dataclass(frozen=True)
class SourceText:
    url: str
    title: str
    text: str
    basis: str


class WebSearch(Protocol):
    def search(self, query: str, *, max_results: int = 5, source: str = "web") -> SearchResponse | None: ...
    def read(self, url: str) -> SourceText | None: ...


class SearchGateway:
    """One door to the outside world, many sources behind it: web + news
    (Tavily, plus the Guardian when a key is configured), pubmed (NCBI),
    scholar (OpenAlex), trials (ClinicalTrials.gov). The keyless sources work
    even with no Tavily key — a fresh install can cite papers on day one."""

    def __init__(self, api_key: str, guardian_key: str = "") -> None:
        self._api_key = api_key
        self._guardian_key = guardian_key
        self._read: dict[str, SourceText] = {}     # the last few sources, so paging doesn't re-fetch

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
                return SearchResponse(query=query, results=merged[:max_results])
            return guardian or tavily
        return self._tavily(query, max_results=max_results, source=source)

    def read(self, url: str) -> SourceText | None:
        """The source's own text. Papers, trials and DOIs go to their keyless
        registries; any other page goes through the search provider's extractor
        — Trellis itself never opens an arbitrary address. None = not reached."""
        ref = url.strip()
        if ref not in self._read:
            found = self._reach(ref)
            if found is None:
                return None
            if len(self._read) >= 8:
                self._read.pop(next(iter(self._read)))
            self._read[ref] = found
        return self._read[ref]

    def _reach(self, ref: str) -> SourceText | None:
        if m := re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", ref) or re.fullmatch(r"(\d{4,9})", ref):
            return pubmed_read(m.group(1))
        if m := re.search(r"(NCT\d{8})", ref, re.IGNORECASE):
            return clinical_trial_read(m.group(1).upper())
        if "doi.org/" in ref or "openalex.org/" in ref:
            found = openalex_read(ref)
            if found is not None:
                return found
        if not ref.lower().startswith(("http://", "https://")):
            return None
        return self._extract(ref)

    def _extract(self, url: str) -> SourceText | None:
        if not self._api_key:
            return None
        try:
            response = httpx.post(
                _TAVILY_EXTRACT_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"urls": [url]},
                timeout=_TIMEOUT * 2,
            )
            response.raise_for_status()
            found = (response.json().get("results") or [])
        except Exception:
            _log.warning("extract failed for %r", url[:80], exc_info=True)
            return None
        text = str(found[0].get("raw_content") or "").strip() if found else ""
        if not text:
            return None
        return SourceText(url=url, title=str(found[0].get("title") or "").strip(), text=text, basis=PAGE)

    def _tavily(self, query: str, *, max_results: int, source: str) -> SearchResponse | None:
        if not self._api_key:
            return None
        try:
            body = {
                "query": query[:400],
                "max_results": max(1, min(10, max_results)),
                "include_answer": False,        # another model's recall is not a source
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
        return SearchResponse(query=query, results=results) if results else None


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
    return SearchResponse(query=query, results=tuple(results))


def _flat(element) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def pubmed_read(pmid: str) -> SourceText | None:
    """The paper itself: the open full text when PubMed Central holds it — the
    qualification that matters is often in the methods or results — otherwise
    the abstract, and the label says which."""
    url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    try:
        resp = httpx.get(f"{_EUTILS}/efetch.fcgi",
                         params={"db": "pubmed", "id": pmid, "retmode": "xml"}, timeout=_TIMEOUT)
        resp.raise_for_status()
        article = ET.fromstring(resp.text).find(".//PubmedArticle")
    except Exception:
        _log.warning("pubmed_read failed for %s", pmid, exc_info=True)
        return None
    if article is None:
        return None
    title = _flat(article.find(".//ArticleTitle"))
    parts = []
    for block in article.findall(".//Abstract/AbstractText"):
        label = (block.get("Label") or "").strip()
        parts.append(f"{label}: {_flat(block)}" if label else _flat(block))
    abstract = "\n\n".join(p for p in parts if p)

    pmc = next((_flat(i) for i in article.findall(".//ArticleIdList/ArticleId") if i.get("IdType") == "pmc"), "")
    if pmc:
        body = _pmc_body(pmc)
        if body:
            return SourceText(url=url, title=title, basis=FULL_TEXT,
                              text=(f"ABSTRACT\n{abstract}\n\n" if abstract else "") + body)
    if not abstract:
        return None
    return SourceText(url=url, title=title, text=abstract, basis=ABSTRACT)


def _pmc_body(pmcid: str) -> str:
    try:
        resp = httpx.get(f"{_EUTILS}/efetch.fcgi",
                         params={"db": "pmc", "id": pmcid.upper().removeprefix("PMC"), "retmode": "xml"},
                         timeout=_TIMEOUT * 2)
        resp.raise_for_status()
        body = ET.fromstring(resp.text).find(".//body")
    except Exception:
        _log.warning("pmc full text failed for %s", pmcid, exc_info=True)
        return ""
    if body is None:
        return ""
    sections = []
    for sec in body.findall("./sec") or [body]:
        heading = _flat(sec.find("./title")).upper()
        paragraphs = [_flat(p) for p in sec.iter("p")]
        text = "\n".join(p for p in paragraphs if p)
        if text:
            sections.append(f"{heading}\n{text}" if heading else text)
    return "\n\n".join(sections)


def openalex_read(ref: str) -> SourceText | None:
    """A scholarly work by DOI or OpenAlex id — its abstract, when one is held."""
    key = ref.strip()
    if "openalex.org/" in key:
        key = key.rsplit("/", 1)[-1]
    try:
        resp = httpx.get(f"https://api.openalex.org/works/{key}", timeout=_TIMEOUT)
        resp.raise_for_status()
        work = resp.json()
    except Exception:
        _log.warning("openalex_read failed for %r", ref[:80], exc_info=True)
        return None
    index = work.get("abstract_inverted_index") or {}
    words = sorted((pos, word) for word, places in index.items() for pos in places)
    abstract = " ".join(word for _, word in words)
    if not abstract:
        return None
    return SourceText(url=ref, title=str(work.get("display_name") or "").strip(), text=abstract, basis=ABSTRACT)


def clinical_trial_read(nct: str) -> SourceText | None:
    try:
        resp = httpx.get(f"https://clinicaltrials.gov/api/v2/studies/{nct}", timeout=_TIMEOUT)
        resp.raise_for_status()
        proto = resp.json().get("protocolSection") or {}
    except Exception:
        _log.warning("clinical_trial_read failed for %s", nct, exc_info=True)
        return None
    describe = proto.get("descriptionModule") or {}
    design = proto.get("designModule") or {}
    outcomes = (proto.get("outcomesModule") or {}).get("primaryOutcomes") or []
    enrolment = (design.get("enrollmentInfo") or {}).get("count")
    blocks = [
        ("STATUS", (proto.get("statusModule") or {}).get("overallStatus", "")),
        ("DESIGN", " · ".join(str(b) for b in (design.get("studyType"), ", ".join(design.get("phases") or []),
                                                f"{enrolment} enrolled" if enrolment else "") if b)),
        ("SUMMARY", describe.get("briefSummary", "")),
        ("DETAIL", describe.get("detailedDescription", "")),
        ("PRIMARY OUTCOMES", "\n".join(f"- {o.get('measure', '')} ({o.get('timeFrame', '')})" for o in outcomes)),
        ("ELIGIBILITY", (proto.get("eligibilityModule") or {}).get("eligibilityCriteria", "")),
        ("RESULTS", "Posted on the registry page." if resp.json().get("hasResults") else "None posted."),
    ]
    text = "\n\n".join(f"{name}\n{str(body).strip()}" for name, body in blocks if str(body).strip())
    if not text:
        return None
    title = str((proto.get("identificationModule") or {}).get("briefTitle") or "").strip()
    return SourceText(url=f"https://clinicaltrials.gov/study/{nct}", title=title, text=text, basis=REGISTRY)


def guardian_search(query: str, api_key: str, *, max_results: int = 5) -> SearchResponse | None:
    """The Guardian Open Platform — quality journalism, structured, citable.
    Newest-first: with a query the API's default order is RELEVANCE over the
    whole archive, which is an archive search, not a news feed."""
    try:
        resp = httpx.get(
            "https://content.guardianapis.com/search",
            params={"q": query[:300], "api-key": api_key,
                    "page-size": max(1, min(10, max_results)),
                    "order-by": "newest",
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
    return SearchResponse(query=query, results=results) if results else None


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
    return SearchResponse(query=query, results=tuple(results)) if results else None


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
    return SearchResponse(query=query, results=tuple(results)) if results else None
