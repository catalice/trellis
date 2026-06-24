# CLAUDE.md — Working rules for Trellis

Read this before touching anything. These rules exist because we learned them the hard way.

---

## What Trellis is

A personal operating system for Cat. Not a running bot with extra features.
Full direction: `docs/DIRECTION.md`. Read it if you haven't.

---

## The one rule that matters most

**Ask before building.** If the user didn't explicitly ask for a feature, don't build it.
Explaining a problem is not a request to solve it. Confirming something works is not a request to extend it.
When in doubt: say what you'd do and ask first.

---

## Python / Claude boundary

This is the most important architectural rule. Getting it wrong is how the system becomes rigid or expensive.

**Python owns:**
- Data persistence (all DB writes)
- Garmin sync and health data
- Readiness scoring (deterministic calculation)
- Structural validation (no two hard sessions in a week, etc.)
- Strength session generation (PT sets the programme — Python generates these anchors)
- Tool execution and side effects
- Anything that must produce the same result every time

**Claude owns:**
- All running session content (easy run, long run, hard run, social run, mobility)
- Coaching decisions (what kind of week, how much load, what to adjust)
- Interpreting what the user actually needs from a message
- Responses and explanations to the user
- Anything requiring judgment, nuance, or knowledge

**The test:** is this a fact or a judgment? Facts live in Python. Judgments go to Claude.

**Never:** hardcode session content in Python. No fixed activation sequences, no templated run blocks, no if/else coaching rules. Claude generates all of that — that's the whole point.

---

## File naming convention

Flat structure, domain prefix. No nested folders.

```
training_models.py     # domain models
training_service.py    # business logic
training_claude.py     # all Claude calls for this domain
training_repo.py       # storage protocol + postgres impl
training_tool.py       # tool schema + handler
```

Adding a new domain (e.g. `learn`) means creating `learn_models.py`, `learn_service.py`, etc. Same pattern every time.

---

## Domain file pattern

Every domain follows this structure:

| File | Contents | Rule |
|------|----------|------|
| `*_models.py` | Frozen dataclasses only | No I/O, no imports from other trellis modules |
| `*_service.py` | Business logic | Never talks to Claude or DB directly — delegates |
| `*_claude.py` | All Claude calls | Prompts as module-level constants; methods return typed data or None on failure |
| `*_repo.py` | Storage | Protocol at top, Postgres impl below |
| `*_tool.py` | Tool wiring | Schema dict + one handler `(user_id, input_dict, now) → str` |

---

## Claude call rules

- Prompts are **module-level constants**, never inline strings inside methods
- Every Claude call has a **typed return** — parse the response, return a dataclass or None
- Parse failures **log a warning and return None** — callers handle gracefully, never crash
- `max_tokens` must be set generously for calls that return full session JSON (8192+)
- Never call Claude for something Python can calculate deterministically

---

## Service rules

- Services are **thin** — validate input, call repo/claude, return formatted string
- No 500-line if/elif chains — one method per action
- Services never talk to Claude directly — that goes through the `*_claude.py` module
- Fallback on Claude failure: **raise**, don't silently return a degraded result

---

## Lean constraints (non-negotiable from day one)

- **One Claude call per turn.** Never add a second call. Add tools instead.
- **Domain routing.** Before the oracle loads context, a lightweight classifier determines which domains are relevant to the message. Training questions load training context. Task questions load task context. Never load everything for everything. This is an architectural constraint, not a future optimisation — it must be present in the initial design.
- **Bounded context.** Insights, patterns, and history enter the oracle as summaries. Never pass raw records. Context size is a cost.
- **Tools as the API surface.** A future UI calls the same tools Telegram does. Design tools accordingly — they are the interface.

---

## Things we keep re-learning (don't repeat)

- **Don't build what wasn't asked for.** The long run day preference feature is a good example of something built without being asked. It wasted time and had to be reverted.
- **Social run content belongs to Claude.** It was hardcoded in Python for months before being fixed. Never again.
- **max_tokens too low truncates JSON silently.** Claude hits the limit mid-JSON and the parse fails. Always set 8192+ for structured responses.
- **`goals` table must be in the reset script.** Easy to forget, causes duplicate goals on re-onboarding.
- **ReadinessBand has no MODERATE.** Valid values: LOW, STEADY, READY, STRONG.
- **Don't revert a commit by amending.** Create a new commit.

---

## Deployment

- Runs via Docker Compose: `docker compose up --build`
- `.env` is gitignored — contains live bot token, API key, secrets. Never log or expose contents.
- DB reset for clean onboarding: `scripts/reset_training.sql` (keeps garmin/health data, wipes training/profile/context)
- Tests: `.venv/bin/pytest tests/ -q`

---

## Current state

Working: oracle, body module (Garmin, readiness, self-report, cycle), training module (continuous coach, plan creation, social run, deload/holiday, arc, PT logging, session completion), mind module (tasks, brain dump, ideas), intelligence layer (pattern engine, insights), goals, Obsidian write.

Not yet built: Obsidian two-way sync, mobility as deliberate pillar, post-run analysis, adaptive routines, UI.

Build order: `docs/DIRECTION.md` section "Build Order".
