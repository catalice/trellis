"""
Domain conversation summariser — Groq (free) first, Anthropic Haiku as the
fallback so a retired Groq model can't silently stop summaries forever.

Runs after every N turns, produces a compact summary of the domain-relevant
conversation, saved to conversation_summaries for future context loading.
"""
from __future__ import annotations

import logging
from typing import Callable
from uuid import UUID

_log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are recording what a conversation covered, for future context.
Focus on the {domain} domain. Past tense: what they said, what was done, what \
was left open. Never state what is currently true or decided — the stores hold \
that, and this will be read days later.

100 words max. Plain text, no headers.\
"""


_CHAR_BUDGET = 16000  # free-tier Groq caps tokens/minute; a fat history 413s


def _trimmed_window(conversation_messages: list[dict]) -> list[dict]:
    """Newest ~16k chars, whole messages only — summaries are about the
    recent arc. If even the newest single message blows the budget (a huge
    dump + trace), hard-truncate it rather than sending it whole: the
    fallback must not defeat the budget it exists for."""
    budget = _CHAR_BUDGET
    trimmed: list[dict] = []
    for m in reversed(conversation_messages):
        c = str(m.get("content") or "")
        if budget - len(c) < 0:
            break
        budget -= len(c)
        trimmed.append(m)
    if not trimmed and conversation_messages:
        newest = dict(conversation_messages[-1])
        newest["content"] = str(newest.get("content") or "")[-_CHAR_BUDGET:]
        trimmed = [newest]
    return list(reversed(trimmed))


def _transcript(conversation_messages: list[dict]) -> str:
    """The conversation as ONE document to record — handed over as chat turns,
    a model continues the chat instead (20 Sep 2026: a 'summary' came back as
    an imitation of the last assistant turn)."""
    lines = [
        f"{'They' if m.get('role') == 'user' else 'Trellis'}: {str(m.get('content') or '').strip()}"
        for m in conversation_messages
    ]
    return "Transcript:\n\n" + "\n\n".join(lines) + "\n\nWrite the record now."


def make_summariser(
    groq_client,
    model: str = "openai/gpt-oss-20b",
    fallback=None,                    # a core_model.ModelConnector — its small model
) -> Callable:
    def _via_groq(system_prompt: str, conversation_messages: list[dict]) -> str | None:
        if groq_client is None:
            return None
        try:
            response = groq_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": _transcript(conversation_messages)},
                ],
                max_tokens=1000,   # a reasoning model spends tokens before it writes
                temperature=0,
            )
            return response.choices[0].message.content.strip() or None
        except Exception:
            _log.warning("groq summarisation failed", exc_info=True)
            return None

    def _via_fallback(system_prompt: str, conversation_messages: list[dict]) -> str | None:
        if fallback is None:
            return None
        try:
            return fallback.complete(
                system_prompt, _transcript(conversation_messages), max_tokens=1024, tier="small",
            ).strip() or None
        except Exception:
            _log.warning("fallback summarisation failed", exc_info=True)
            return None

    def summarise(user_id: UUID, domain: str, history) -> None:
        try:
            turns = history.recent(user_id, limit=40)
            if not turns:
                return
            conversation_messages = _trimmed_window(history.to_messages(turns))
            system_prompt = _SYSTEM_PROMPT.format(domain=domain)
            summary = (_via_groq(system_prompt, conversation_messages)
                       or _via_fallback(system_prompt, conversation_messages))
            if not summary:
                return
            # turns_covered stores the TOTAL turn count at summarisation time —
            # it's the cursor max_turns_covered() reads, not the window size.
            history.save_domain_summary(user_id, domain, summary, history.turn_count(user_id))
        except Exception:
            _log.warning("summarisation failed for domain '%s'", domain, exc_info=True)

    return summarise
