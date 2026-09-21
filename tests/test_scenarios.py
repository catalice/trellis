"""
Scenarios, run two ways (see harness.py).

  pytest tests/test_scenarios.py                 scripted model — always runs
  TRELLIS_EVAL=1 pytest tests/test_scenarios.py  also asks the REAL model
                                                 (needs the provider's key; costs a little)

A scenario's `checks` are about outcomes — what was done, what the person was
told — so the same checks judge the script and the real model. Add a scenario
immediately before fixing the fault it shows.
"""
from __future__ import annotations

import os
import re

import pytest

from datetime import datetime, timezone
from uuid import uuid4

from harness import Outcome, Scenario, ScriptedModel, Step, run, tool
from trellis.core_actions import done, failed
from trellis.domain_focus_tool import WEB_SEARCH_TOOL, handle_web_search
from trellis.infra_search import ABSTRACT, SearchResponse, SearchResult, SourceText

SAVE_NOTE = tool("save_note", "Save a note for them. Call when they ask you to note or remember something.",
                 text="The note, in their words.")
SET_REMINDER = tool("set_reminder", "Set a reminder for them at a time.",
                    label="What to remind them of.", when="When, as they said it.")


def _said(pattern: str):
    def check(o: Outcome) -> None:
        assert re.search(pattern, o.reply, re.I), f"reply never said /{pattern}/: {o.reply!r}"
    return check


def _called(name: str, times: int | None = 1, **containing: str):
    """times=None: at least once."""
    def check(o: Outcome) -> None:
        calls = o.called(name)
        if times is None:
            assert calls, f"{name} was never called: {o.calls}"
        else:
            assert len(calls) == times, f"{name} called {len(calls)}x, expected {times}: {o.calls}"
        for key, fragment in containing.items():
            assert any(fragment.lower() in str(c.input.get(key, "")).lower() for c in calls), (key, fragment, calls)
    return check


class _Library:
    """A stand-in for the outside world, behind the REAL web_search tool: its
    real description, its real wording of a listing and of a source's text."""
    PAPER = "https://pubmed.ncbi.nlm.nih.gov/100001/"

    def __init__(self, readable: bool = True) -> None:
        self._readable = readable

    def search(self, query, *, max_results=5, source="web"):
        return SearchResponse(query=query, results=(SearchResult(
            "Food and the absorption of ibuprofen: a crossover study", self.PAPER, "Clin Pharm · 2019"),))

    def read(self, url):
        if not self._readable:
            return None
        return SourceText(self.PAPER, "Food and the absorption of ibuprofen: a crossover study", basis=ABSTRACT, text=(
            "METHODS: 24 healthy adults, single 400 mg dose, fasted vs after a standard meal.\n"
            "RESULTS: Food delayed peak concentration by about 40 minutes. Total absorption was unchanged.\n"
            "CONCLUSIONS: Food slows but does not reduce absorption."))


def _web_search(library: _Library):
    return (WEB_SEARCH_TOOL, lambda inp: handle_web_search(
        uuid4(), inp, datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc), web_search=library))


def _read_before_answering(o: Outcome) -> None:
    assert any(str(c.input.get("read", "")).strip() for c in o.called("web_search")), (
        f"explained without reading a source: {o.calls}")


def _spoke(o: Outcome) -> None:
    assert o.reply.strip(), "the person was told nothing"


SCENARIOS = [
    Scenario(
        name="a question and an action in one message are both handled",
        message="Save a note that the boiler needs servicing. Also, what is 12 times 12?",
        tools={"save_note": (SAVE_NOTE, done("Saved."))},
        script=[Step(text="12 times 12 is 144.", tools=(("save_note", {"text": "the boiler needs servicing"}),)),
                Step(text="And I've saved the boiler note.")],
        checks=[_called("save_note", text="boiler"), _said(r"\b144\b")],
        scripted_checks=[
            # everything the model wrote, before and after the tool, reaches them, in order
            lambda o: _said(r"144\.\s+And I've saved")(o),
            # their message rides behind the tool results, so it is the last thing read
            lambda o: _said("boiler")(Outcome(o.model.notes[0] or "", [], o.result, o.model)),
        ],
    ),
    Scenario(
        name="two requests in one message are both carried out",
        message="Note that the quince needs planting, and remind me on Friday morning to call the electrician.",
        tools={"save_note": (SAVE_NOTE, done("Saved.")), "set_reminder": (SET_REMINDER, done("Reminder set."))},
        script=[Step(tools=(("save_note", {"text": "the quince needs planting"}),
                            ("set_reminder", {"label": "call the electrician", "when": "Friday morning"}))),
                Step(text="Noted the quince, and I'll remind you Friday morning about the electrician.")],
        checks=[_called("save_note", text="quince"), _called("set_reminder", label="electrician"), _spoke],
    ),
    Scenario(
        # Answer completeness, measured before anything is built for it (an earlier
        # checking step degraded replies). Three parts: a question, an action, and
        # a second question that depends on the first.
        name="three parts in one message are all answered",
        message=("Remind me on Saturday morning about the long run. Also, what is 15% of 80? "
                 "And is 12 more or less than that?"),
        tools={"set_reminder": (SET_REMINDER, done("Reminder set."))},
        script=[Step(tools=(("set_reminder", {"label": "the long run", "when": "Saturday morning"}),)),
                Step(text="Reminder's set for Saturday morning. 15% of 80 is 12 — so 12 is exactly that, neither more nor less.")],
        checks=[_called("set_reminder", label="long run"), _said(r"\b12\b"),
                _said(r"same|exactly|equal|neither|identical")],
    ),
    Scenario(
        name="a question buried after two actions is still answered",
        message=("Note that the quince needs planting and note that the gate latch is broken. "
                 "By the way, how many days are there in a leap year?"),
        tools={"save_note": (SAVE_NOTE, done("Saved."))},
        script=[Step(tools=(("save_note", {"text": "the quince needs planting"}),
                            ("save_note", {"text": "the gate latch is broken"}))),
                Step(text="Both noted. A leap year has 366 days.")],
        checks=[_called("save_note", times=2), _said(r"\b366\b")],
    ),
    Scenario(
        name="a tool that reports failure is reported as failure",
        message="Save a note that the boiler needs servicing.",
        tools={"save_note": (SAVE_NOTE, failed("Save failed; nothing changed."))},
        script=[Step(tools=(("save_note", {"text": "the boiler needs servicing"}),)),
                Step(text="That didn't save — the note wasn't stored. Want me to try again?"),
                Step(text="That didn't save — the note wasn't stored. Want me to try again?")],   # the rewrite: unchanged
        checks=[_called("save_note"), _said(r"didn't|did not|couldn't|could not|fail|wasn't|was not|unable")],
    ),
    Scenario(
        name="a model that goes silent after acting is asked to speak",
        message="Save a note that the boiler needs servicing.",
        tools={"save_note": (SAVE_NOTE, done("Saved."))},
        script=[Step(tools=(("save_note", {"text": "the boiler needs servicing"}),)),
                Step(text=""),                                   # ends its turn having said nothing
                Step(text="Saved your note about the boiler.")],
        checks=[_called("save_note"), _spoke],
        scripted_checks=[lambda o: (len(o.model.said) == 1 and o.model.steps_left == 0) or pytest.fail(
            f"expected exactly one nudge: {o.model.said}")],
        real_model=False,
    ),
    # --- evidence -------------------------------------------------------------------
    Scenario(
        name="how something works is answered from a source that was read",
        message="Does taking ibuprofen with food change how much of it gets absorbed?",
        tools={"web_search": _web_search(_Library())}, read_only=frozenset({"web_search"}),
        script=[Step(tools=(("web_search", {"query": "ibuprofen food absorption", "source": "pubmed"}),)),
                Step(tools=(("web_search", {"read": _Library.PAPER}),)),
                Step(text="From the abstract of a 2019 crossover study (I couldn't read the full paper): food "
                          "delayed the peak by about 40 minutes, but the total absorbed was unchanged.")],
        checks=[_read_before_answering, _said(r"delay|slow|later"), _said(r"unchanged|same|not reduce|doesn't reduce|does not reduce")],
    ),
    Scenario(
        name="a source that could not be read is not described",
        message="Does taking ibuprofen with food change how much of it gets absorbed?",
        tools={"web_search": _web_search(_Library(readable=False))}, read_only=frozenset({"web_search"}),
        script=[Step(tools=(("web_search", {"query": "ibuprofen food absorption", "source": "pubmed"}),)),
                Step(tools=(("web_search", {"read": _Library.PAPER}),)),
                Step(text="I found a study on exactly this but couldn't read it, so I can't tell you what it found. "
                          "The leaflet or a pharmacist will have the answer.")],
        checks=[_read_before_answering,
                _said(r"couldn't|could not|can't|cannot|unable|wasn't able|not able|didn't manage|no access")],
    ),
    Scenario(
        name="a challenged explanation is rechecked, not replaced with another story",
        earlier=[{"role": "user", "content": "Does food change how much ibuprofen gets absorbed?"},
                 {"role": "assistant", "content": "Yes — food cuts the amount absorbed by about half, so take it on an empty stomach."}],
        message="That doesn't sound right. The leaflet says to take it with food.",
        tools={"web_search": _web_search(_Library())}, read_only=frozenset({"web_search"}),
        script=[Step(tools=(("web_search", {"query": "ibuprofen food absorption", "source": "pubmed"}),)),
                Step(tools=(("web_search", {"read": _Library.PAPER}),)),
                Step(text="You're right to push — I was wrong, and I hadn't read a source. The abstract of a 2019 "
                          "crossover study says food delays the peak by about 40 minutes and total absorption is unchanged.")],
        checks=[_read_before_answering, _said(r"wrong|mistake|incorrect|shouldn't have|should not have|hadn't|had not")],
    ),
    Scenario(
        name="what the watch observed and what they reported are told apart",
        message="How did I sleep last night?",
        context=("Today: Monday 2 March 2026, 09:00.\n[Wellbeing]\n"
                 "REPORTED by them — state logs (last 7 days); e/m scores are read from their words:\n"
                 "  [a1] 02 Mar 07:40 e2 — slept terribly, awake for hours\n"
                 "OBSERVED by the watch — recent Garmin readiness: sleep 84 (7.9h), HRV 58"),
        tools={},
        script=[Step(text="The watch recorded 7.9 hours with a score of 84 — but you said this morning you slept "
                          "terribly and were awake for hours. Those don't match; your account is the one I'd go with.")],
        checks=[_said(r"7\.9|84"), _said(r"you said|you told|you logged|you wrote|you reported|you noted|your (own )?(note|log|words|account)")],
    ),
    Scenario(
        name="a handler that crashes does not crash the turn",
        message="Save a note that the boiler needs servicing.",
        tools={"save_note": (SAVE_NOTE, RuntimeError("database is down"))},
        script=[Step(tools=(("save_note", {"text": "the boiler needs servicing"}),)),
                Step(text="That hit an error — I can't tell whether it saved."),
                Step(text="That hit an error — I can't tell whether it saved.")],              # the rewrite: unchanged
        # At least once: the first real-model run (20 Sep 2026) called it TWICE — the
        # engine's failure text says "try again in a moment", and the model did, blind,
        # with no way to know whether the first attempt had taken effect. Stage 3's
        # "unknown outcome, never retried blindly" starts from that observation.
        checks=[_called("save_note", times=None), _spoke],
        scripted_checks=[lambda o: "OUTCOME UNKNOWN" in o.model.results_given[0][0].content or pytest.fail(
            "the model was not told the outcome is unknown")],
    ),
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_scripted(scenario: Scenario) -> None:
    outcome = run(scenario)
    assert isinstance(outcome.model, ScriptedModel)
    for check in [*scenario.checks, *scenario.scripted_checks]:
        check(outcome)


def _real_model():
    """The provider chosen in configuration — never named here."""
    if os.getenv("TRELLIS_EVAL") != "1":          # exactly "1": "0" and "false" must not spend money
        pytest.skip("real-model evaluation is opt-in: TRELLIS_EVAL=1")
    from trellis.core_config import Settings
    from trellis.core_main import build_model
    settings = Settings.from_env()
    if settings.model_provider == "anthropic" and not settings.anthropic_api_key:
        pytest.skip("no credentials for the selected model provider")
    return build_model(settings)


@pytest.mark.parametrize("scenario", [s for s in SCENARIOS if s.real_model], ids=lambda s: s.name)
def test_real_model(scenario: Scenario) -> None:
    outcome = run(scenario, model=_real_model())
    for check in scenario.checks:
        check(outcome)


# --- the harness itself ----------------------------------------------------------------

def test_an_attempt_that_changes_something_then_raises_is_still_on_record():
    """'The action happened, the acknowledgement was lost' — the case stage 3 is about."""
    from harness import SimulatedTools
    stored = []

    def save_then_time_out(args):
        stored.append(args["text"])
        raise TimeoutError("no acknowledgement")

    tools = SimulatedTools({"save_note": (SAVE_NOTE, save_then_time_out)})
    with pytest.raises(TimeoutError):
        tools.handlers["save_note"]({"text": "boiler"})
    assert stored == ["boiler"]                                     # the effect happened
    assert len(tools.calls) == 1 and isinstance(tools.calls[0].raised, TimeoutError)
    assert tools.calls[0].result is None


@pytest.mark.parametrize("value", ["0", "false", "no", "", "true", "yes"])
def test_only_the_documented_value_enables_paid_evaluation(monkeypatch, value):
    monkeypatch.setenv("TRELLIS_EVAL", value)
    monkeypatch.setattr("trellis.core_main.build_model",
                        lambda settings: pytest.fail("a disabled setting reached the real-model factory"))
    with pytest.raises(pytest.skip.Exception):
        _real_model()
