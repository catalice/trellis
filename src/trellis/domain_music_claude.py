"""Music domain Claude calls — the judgment layer for building a set.

Recommendation *retrieval* is deterministic (meaning-index recall over tracks —
Python). This module holds the one thing that is genuinely judgment, not a fact:
arranging a shortlist into an ordered set with an energy arc, and naming it. Used
by the deliberate "build me a set for X" path — analogous to how brain_dump calls
Claude to synthesise. The conversational "what goes with this?" path needs no call
here: the oracle (companion persona) curates the shortlist inline.

Prompts are module-level constants; every call returns typed data or None on
failure (the caller then falls back to an unarranged set).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from anthropic import Anthropic

_log = logging.getLogger(__name__)

_ARRANGE_SYSTEM = """\
You are a DJ's ear helping arrange a set. You are given a vibe the user is after \
and a numbered shortlist of tracks pulled from their OWN library. Choose which \
tracks belong and put them in an order that flows as an energy arc — not sorted \
by genre or tempo, but by feeling: where the set opens, where it lifts, where it \
lands. Bridging across genres on mood is the whole point; eclectic is good.

Return ONLY valid JSON matching this structure exactly:
{
  "name": "short evocative playlist name",
  "description": "one sentence on the arc / the feeling",
  "order": [3, 0, 5, 1],
  "note": "one short line to say back to them about the journey"
}

Rules:
- "order": indices into the shortlist (the numbers shown), in play order. Include \
only tracks that genuinely serve the vibe — dropping weak fits is better than \
forcing all of them. Never invent an index that wasn't offered.
- Keep the set tight: a handful to ~12 tracks unless the vibe wants more.
- "name"/"description"/"note": warm and specific to the vibe and these tracks, \
never generic. No hashtags, no emoji spam.
- If none of the shortlist fits the vibe, return an empty "order" array.\
"""


@dataclass(frozen=True)
class ArrangedSet:
    """A Claude-arranged set: an ordering over the shortlist indices, plus naming.
    order holds indices into the candidate list passed to arrange()."""
    name: str
    description: str
    order: tuple[int, ...]
    note: str


class MusicClaude:
    def __init__(self, client: Anthropic, model: str) -> None:
        self._client = client
        self._model = model

    def arrange_set(self, vibe: str, candidate_labels: list[str]) -> ArrangedSet | None:
        """Given a vibe and a numbered shortlist (index -> 'name — artists'), pick
        and order the set. Returns None on any failure so the caller can fall back
        to the raw shortlist order."""
        if not candidate_labels:
            return None
        shortlist = "\n".join(f"{i}: {label}" for i, label in enumerate(candidate_labels))
        user = f"Vibe: {vibe}\n\nShortlist:\n{shortlist}"
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=8192,
                system=_ARRANGE_SYSTEM,
                messages=[{"role": "user", "content": user}],
            )
            raw = response.content[0].text.strip()
            return _parse_arranged(raw, len(candidate_labels))
        except Exception:
            _log.warning("MusicClaude.arrange_set failed", exc_info=True)
            return None


def _strip_json_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _parse_arranged(raw: str, candidate_count: int) -> ArrangedSet | None:
    try:
        data = json.loads(_strip_json_fences(raw))
    except json.JSONDecodeError:
        _log.warning("MusicClaude: arrange response was not valid JSON")
        return None

    order: list[int] = []
    seen: set[int] = set()
    for value in data.get("order", []):
        try:
            idx = int(value)
        except (TypeError, ValueError):
            continue
        # Drop out-of-range or duplicate indices — Claude must only reorder what
        # was offered.
        if 0 <= idx < candidate_count and idx not in seen:
            seen.add(idx)
            order.append(idx)

    name = str(data.get("name", "")).strip() or "Untitled set"
    description = str(data.get("description", "")).strip()
    note = str(data.get("note", "")).strip()
    return ArrangedSet(
        name=name,
        description=description,
        order=tuple(order),
        note=note,
    )
