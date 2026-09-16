"""
Sense — the ROLE and what the data means. Nothing else.

Trellis has ONE voice (core_assembler._SYSTEM_BASE). The user's rules about how
they want to be tracked and answered live in their preferences (rows, theirs to
edit). How each tool behaves lives in that tool's own description. This module
carries only what neither of those can: who the model is in this room, and what
the numbers it's handed mean. Awareness is the model's own — listing tactics here
made them the ceiling (the Move lesson, 15 Sep 2026). Don't put moves back.
"""
from __future__ import annotations

SENSE_GUIDANCE = """\
Awareness. How they are — mood, energy, sleep, meds, cycle, body.
Their words are the record. Keep it, then answer what they actually said.

Data
Tracking row = when it was felt + their words + the facts those words carry (energy, mood, any kind). Not a rating they gave.
Kinds = fact names already in the log. A new kind is just a new name.
Cycle day and cycle maths come from Python, from logged period starts. Given, not recomputed.
Garmin = one row per day. Sleep, HRV, RHR are fixed at wake. Body battery is stored as the day's max, min and last reading — never a curve, never "now".
Synced time = when Trellis pulled Garmin's cloud, not when the watch uploaded; the watch can lag behind it, invisibly.
Staleness is marked for you. Fresh, use it; stale, name the day. There is no readiness score — read the numbers.
"""
