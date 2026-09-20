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

from harness import Outcome, Scenario, ScriptedModel, Step, run, tool

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


def _spoke(o: Outcome) -> None:
    assert o.reply.strip(), "the person was told nothing"


SCENARIOS = [
    Scenario(
        name="a question and an action in one message are both handled",
        message="Save a note that the boiler needs servicing. Also, what is 12 times 12?",
        tools={"save_note": (SAVE_NOTE, "Saved.")},
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
        tools={"save_note": (SAVE_NOTE, "Saved."), "set_reminder": (SET_REMINDER, "Reminder set.")},
        script=[Step(tools=(("save_note", {"text": "the quince needs planting"}),
                            ("set_reminder", {"label": "call the electrician", "when": "Friday morning"}))),
                Step(text="Noted the quince, and I'll remind you Friday morning about the electrician.")],
        checks=[_called("save_note", text="quince"), _called("set_reminder", label="electrician"), _spoke],
    ),
    Scenario(
        name="a tool that reports failure is reported as failure",
        message="Save a note that the boiler needs servicing.",
        tools={"save_note": (SAVE_NOTE, "Save failed; nothing changed.")},
        script=[Step(tools=(("save_note", {"text": "the boiler needs servicing"}),)),
                Step(text="That didn't save — the note wasn't stored. Want me to try again?")],
        checks=[_called("save_note"), _said(r"didn't|did not|couldn't|could not|fail|wasn't|was not|unable")],
    ),
    Scenario(
        name="a model that goes silent after acting is asked to speak",
        message="Save a note that the boiler needs servicing.",
        tools={"save_note": (SAVE_NOTE, "Saved.")},
        script=[Step(tools=(("save_note", {"text": "the boiler needs servicing"}),)),
                Step(text=""),                                   # ends its turn having said nothing
                Step(text="Saved your note about the boiler.")],
        checks=[_called("save_note"), _spoke],
        scripted_checks=[lambda o: (len(o.model.said) == 1 and o.model.steps_left == 0) or pytest.fail(
            f"expected exactly one nudge: {o.model.said}")],
        real_model=False,
    ),
    Scenario(
        name="a handler that crashes does not crash the turn",
        message="Save a note that the boiler needs servicing.",
        tools={"save_note": (SAVE_NOTE, RuntimeError("database is down"))},
        script=[Step(tools=(("save_note", {"text": "the boiler needs servicing"}),)),
                Step(text="Something went wrong saving that — it isn't stored.")],
        # At least once: the first real-model run (20 Sep 2026) called it TWICE — the
        # engine's failure text says "try again in a moment", and the model did, blind,
        # with no way to know whether the first attempt had taken effect. Stage 3's
        # "unknown outcome, never retried blindly" starts from that observation.
        checks=[_called("save_note", times=None), _spoke],
        scripted_checks=[lambda o: "went wrong" in o.model.results_given[0][0].content or pytest.fail(
            "the model was not told the tool failed")],
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
    if not os.getenv("TRELLIS_EVAL"):
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
