"""Explaining from a source means having read it. A search is a LISTING — titles
and fragments — and used to be all there was: PubMed gave no abstract, other
results were cut to 200 characters, and the search provider's own synthesised
answer was passed through as if it were a source. Now a source's text can be
read, and what comes back says how much of it was reached."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx

from trellis import infra_search
from trellis.domain_focus_tool import handle_web_search
from trellis.infra_search import ABSTRACT, FULL_TEXT, PAGE, REGISTRY, SearchGateway, SourceText

NOW = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
UID = uuid4()

_PUBMED = """<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>
<ArticleTitle>Timing of a dose and <i>absorption</i></ArticleTitle>
<Abstract><AbstractText Label="METHODS">Twelve adults, crossover.</AbstractText>
<AbstractText Label="RESULTS">Absorption was delayed with food, not reduced.</AbstractText></Abstract>
</Article></MedlineCitation><PubmedData><ArticleIdList>
<ArticleId IdType="pubmed">123456</ArticleId>{pmc}</ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""

_PMC = """<pmc-articleset><article><body>
<sec><title>Methods</title><p>Participants fasted for ten hours.</p></sec>
<sec><title>Results</title><p>The delay was ninety minutes.</p><sec><title>Subgroup</title><p>Older adults: no delay.</p></sec></sec>
</body></article></pmc-articleset>"""


def _reply(text="", json=None):
    return SimpleNamespace(text=text, json=lambda: json or {}, raise_for_status=lambda: None)


def _routes(monkeypatch, *, get=None, post=None):
    calls = []
    def fake_get(url, **kw):
        calls.append(("GET", url, kw.get("params")))
        return get(url, kw.get("params") or {})
    def fake_post(url, **kw):
        calls.append(("POST", url, kw.get("json")))
        return post(url, kw.get("json") or {})
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


class TestAPaper:
    def test_the_abstract_is_read_with_its_labelled_sections(self, monkeypatch):
        _routes(monkeypatch, get=lambda url, p: _reply(_PUBMED.format(pmc="")))
        found = SearchGateway("").read("https://pubmed.ncbi.nlm.nih.gov/123456/")
        assert found.basis == ABSTRACT
        assert "RESULTS: Absorption was delayed with food, not reduced." in found.text
        assert found.title == "Timing of a dose and absorption"          # inline markup flattened

    def test_open_full_text_is_read_when_it_exists(self, monkeypatch):
        """The qualification that matters is often in the methods or results."""
        def get(url, p):
            return _reply(_PMC if p.get("db") == "pmc" else _PUBMED.format(pmc='<ArticleId IdType="pmc">PMC777</ArticleId>'))
        calls = _routes(monkeypatch, get=get)
        found = SearchGateway("").read("123456")                           # a bare PubMed id works too
        assert found.basis == FULL_TEXT
        assert "METHODS\nParticipants fasted for ten hours." in found.text
        assert "Older adults: no delay." in found.text                    # nested sections are kept
        assert calls[1][2]["id"] == "777"

    def test_a_paper_whose_full_text_is_closed_falls_back_to_the_abstract_and_says_so(self, monkeypatch):
        def get(url, p):
            if p.get("db") == "pmc":
                return _reply("<pmc-articleset><article><front/></article></pmc-articleset>")
            return _reply(_PUBMED.format(pmc='<ArticleId IdType="pmc">PMC777</ArticleId>'))
        _routes(monkeypatch, get=get)
        assert SearchGateway("").read("123456").basis == ABSTRACT


class TestOtherSources:
    def test_a_doi_gives_its_abstract_in_reading_order(self, monkeypatch):
        work = {"display_name": "On ports", "abstract_inverted_index": {"capitals": [2], "Ports": [0], "become": [1]}}
        _routes(monkeypatch, get=lambda url, p: _reply(json=work))
        found = SearchGateway("").read("https://doi.org/10.1000/xyz")
        assert (found.text, found.basis) == ("Ports become capitals", ABSTRACT)

    def test_a_trial_gives_its_registry_record(self, monkeypatch):
        study = {"hasResults": False, "protocolSection": {
            "identificationModule": {"briefTitle": "A trial"}, "statusModule": {"overallStatus": "RECRUITING"},
            "descriptionModule": {"briefSummary": "Compares two schedules."},
            "outcomesModule": {"primaryOutcomes": [{"measure": "Sleep onset", "timeFrame": "4 weeks"}]}}}
        _routes(monkeypatch, get=lambda url, p: _reply(json=study))
        found = SearchGateway("").read("https://clinicaltrials.gov/study/NCT01234567")
        assert found.basis == REGISTRY
        assert "Sleep onset (4 weeks)" in found.text and "None posted." in found.text

    def test_any_other_page_goes_through_the_provider_never_fetched_directly(self, monkeypatch):
        def no_get(url, p): raise AssertionError("Trellis must not open an arbitrary address itself")
        calls = _routes(monkeypatch, get=no_get, post=lambda url, body: _reply(
            json={"results": [{"title": "A page", "raw_content": "The page's words."}]}))
        found = SearchGateway("key").read("https://example.org/article")
        assert (found.text, found.basis) == ("The page's words.", PAGE)
        assert calls == [("POST", infra_search._TAVILY_EXTRACT_URL, {"urls": ["https://example.org/article"]})]

    def test_not_a_link_is_not_read(self, monkeypatch):
        _routes(monkeypatch)
        assert SearchGateway("key").read("file:///etc/passwd") is None
        assert SearchGateway("key").read("localhost:5432") is None

    def test_paging_a_source_fetches_it_once(self, monkeypatch):
        calls = _routes(monkeypatch, post=lambda url, body: _reply(json={"results": [{"raw_content": "words"}]}))
        gateway = SearchGateway("key")
        gateway.read("https://example.org/a"); gateway.read("https://example.org/a")
        assert len(calls) == 1


class TestSearchIsAListing:
    def test_the_providers_synthesised_answer_is_not_requested(self, monkeypatch):
        """Another model's recall, handed over first and unlabelled, read as a source."""
        calls = _routes(monkeypatch, post=lambda url, body: _reply(
            json={"answer": "Confident summary.", "results": [{"title": "T", "url": "https://a.org", "content": "frag"}]}))
        found = SearchGateway("key").search("anything")
        assert calls[0][2]["include_answer"] is False
        assert not hasattr(found, "answer")


class _Reads:
    def __init__(self, found): self.found = found
    def read(self, url): return self.found


class TestWhatTheModelIsTold:
    def test_an_abstract_says_what_was_not_read(self):
        found = SourceText("https://pubmed.ncbi.nlm.nih.gov/1/", "A paper", "RESULTS: delayed.", ABSTRACT)
        reply = handle_web_search(UID, {"read": found.url}, NOW, web_search=_Reads(found))
        assert reply.startswith("SOURCE TEXT — ABSTRACT ONLY — A paper")
        assert "methods, results and caveats beyond it were NOT read" in reply
        assert "RESULTS: delayed." in reply

    def test_a_long_source_is_read_in_parts_and_says_where_the_rest_is(self):
        found = SourceText("https://a.org", "Long", "x" * 13000, FULL_TEXT)
        first = handle_web_search(UID, {"read": found.url}, NOW, web_search=_Reads(found))
        last = handle_web_search(UID, {"read": found.url, "page": 3}, NOW, web_search=_Reads(found))
        assert "Part 1 of 3 — the rest: read=https://a.org page=2" in first
        assert "Part 3 of 3" in last and "the rest" not in last
        assert "no part 9" in handle_web_search(UID, {"read": found.url, "page": 9}, NOW, web_search=_Reads(found))

    def test_a_source_that_could_not_be_reached_is_never_described(self):
        reply = handle_web_search(UID, {"read": "https://a.org/paywalled"}, NOW, web_search=_Reads(None))
        assert reply.startswith("NOT READ")
        assert "Nothing from it can be stated" in reply
