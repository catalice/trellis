"""
Domain conversation summariser using Groq.

Runs after every N turns, produces a compact summary of the domain-relevant
conversation, saved to conversation_summaries table for future context loading.

Usage in main.py:
    from trellis.summariser import make_summariser
    from groq import Groq as GroqClient

    if settings.groq_api_key:
        groq_client = GroqClient(api_key=settings.groq_api_key)
        summariser = make_summariser(groq_client)
    else:
        summariser = None

    assembler = Assembler(..., summariser=summariser)
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


def make_summariser(
    groq_client,
    model: str = "openai/gpt-oss-20b",
) -> Callable:
    def summarise(user_id: UUID, domain: str, history) -> None:
        try:
            turns = history.recent(user_id, limit=40)
            if not turns:
                return
            conversation_messages = history.to_messages(turns)
            system_prompt = _SYSTEM_PROMPT.format(domain=domain)
            response = groq_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *conversation_messages,
                ],
                max_tokens=300,
                temperature=0,
            )
            summary = response.choices[0].message.content.strip()
            # turns_covered stores the TOTAL turn count at summarisation time —
            # it's the cursor max_turns_covered() reads, not the window size.
            history.save_domain_summary(user_id, domain, summary, history.turn_count(user_id))
        except Exception:
            _log.warning("groq summarisation failed for domain '%s'", domain, exc_info=True)

    return summarise
