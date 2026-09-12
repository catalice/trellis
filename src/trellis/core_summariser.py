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
You are summarising a conversation for future context.
Focus on the {domain} domain. Capture:
- Key decisions made
- Current state (what's in progress, what was completed)
- Important context that would be useful in future conversations

Be concise. 150 words max. Plain text, no headers.\
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


def make_summariser(
    groq_client,
    model: str = "openai/gpt-oss-20b",
    fallback_client=None,             # Anthropic client — Haiku fallback
    fallback_model: str = "claude-haiku-4-5-20251001",
) -> Callable:
    def _via_groq(system_prompt: str, conversation_messages: list[dict]) -> str | None:
        if groq_client is None:
            return None
        try:
            response = groq_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *conversation_messages,
                ],
                max_tokens=300,
                temperature=0,
            )
            return response.choices[0].message.content.strip() or None
        except Exception:
            _log.warning("groq summarisation failed", exc_info=True)
            return None

    def _via_fallback(system_prompt: str, conversation_messages: list[dict]) -> str | None:
        if fallback_client is None:
            return None
        try:
            messages = list(conversation_messages)
            if messages and messages[0].get("role") != "user":
                messages.insert(0, {"role": "user", "content": "[conversation continues]"})
            response = fallback_client.messages.create(
                model=fallback_model,
                max_tokens=1024,
                system=system_prompt,
                messages=messages,
            )
            texts = [
                b.text for b in response.content
                if getattr(b, "type", None) == "text" and getattr(b, "text", "")
            ]
            return "\n".join(t.strip() for t in texts if t.strip()).strip() or None
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
