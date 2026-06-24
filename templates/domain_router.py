"""
TEMPLATE: domain_router.py
Actual file: src/trellis/router.py  (one per project, not per domain)

The router decides which domains are relevant to a message BEFORE the oracle
loads context or tools. This keeps the oracle call lean — a task question
never loads Garmin data, a training question never loads the learning curriculum.

Rules:
- Classification must be cheap — keyword match first, Claude fallback only if needed
- Returns a set of domain names — oracle uses this to load context and filter tools
- Domains: "body", "training", "mind", "learn", "goals", "meta"
- When in doubt, include — false positives are cheap, false negatives break the oracle
- "meta" catches system questions (what can you do, help, settings)
"""
from __future__ import annotations

import re

DOMAINS = {"body", "training", "mind", "learn", "goals", "meta"}

# Keywords that signal each domain strongly enough to skip Claude
_SIGNALS: dict[str, list[str]] = {
    "body": [
        "sleep", "hrv", "readiness", "body battery", "heart rate", "soreness",
        "tired", "energy", "cycle", "period", "morning", "health", "garmin",
    ],
    "training": [
        "run", "running", "plan", "session", "training", "workout", "social run",
        "long run", "easy run", "hard run", "strength", "pt", "personal training",
        "mobility", "race", "5k", "10k", "half marathon", "marathon", "km", "pace",
    ],
    "mind": [
        "task", "tasks", "todo", "to do", "remind", "reminder", "idea", "capture",
        "brain dump", "note", "think", "thought", "later",
    ],
    "learn": [
        "learn", "learning", "curriculum", "read", "topic", "chapter", "history",
        "science", "philosophy", "politics", "culture",
    ],
    "goals": [
        "goal", "goals", "target", "achieve", "milestone", "race date",
    ],
    "meta": [
        "what can you", "help", "how do i", "settings", "trellis",
    ],
}


def route(message: str) -> set[str]:
    """
    Returns the set of relevant domains for a message.
    Fast keyword-based — no Claude call needed for most messages.
    """
    text = message.lower()
    matched = {
        domain
        for domain, keywords in _SIGNALS.items()
        if any(kw in text for kw in keywords)
    }
    # Always include meta for very short or ambiguous messages
    if not matched or len(text.split()) <= 3:
        matched.add("meta")
    return matched


# --- Context loaders ----------------------------------------------------
# Each domain registers a loader: (user_id, as_of) -> str | None
# Oracle calls only the loaders for matched domains.
#
# Register in main.py:
#   context_loaders = {
#       "body": lambda uid, dt: health_context(uid, dt),
#       "training": lambda uid, dt: training_context(uid, dt),
#       ...
#   }
#
# Oracle assembly:
#   domains = route(message)
#   context_parts = [context_loaders[d](user_id, now) for d in domains if d in context_loaders]
#   context = "\n\n".join(p for p in context_parts if p)
