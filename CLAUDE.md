# CLAUDE.md — Working rules for Trellis

Read this before touching anything. These rules exist because we learned them the hard way.

---

## What Trellis is

Trellis is a second brain. Not a coaching bot. Not a task manager. A persistent external mind that holds what Cat's brain can't — ideas, tasks, goals, things worth remembering — and synthesises it into something useful.

Coaching (training) and structured learning are modules that plug into the second brain. The second brain is the system. Everything else is a room inside it.

**Three modes:**
- **Second brain** — capture, synthesise, triage, retrieve, build. The front door. Default when nothing else is signalled.
- **Training** — physical coaching module. Plans, Garmin, readiness, arc. Add-on, not the core.
- **Learn** — deliberate knowledge building. Threads, synthesis, understanding over time.

Full direction: `docs/DIRECTION.md`.

---

## The one rule that matters most

**Ask before building.** If the user didn't explicitly ask for a feature, don't build it.
Explaining a problem is not a request to solve it. Confirming something works is not a request to extend it.
When in doubt: say what you'd do and ask first.

---

## Python / Claude boundary

**Python owns:**
- Data persistence (all DB writes)
- Garmin sync and health data
- Readiness scoring (deterministic calculation)
- Structural validation
- Tool execution and side effects
- Anything that must produce the same result every time

**Claude owns:**
- Brain dump synthesis — triage, clean up, surface actions and project seeds
- All running session content (easy run, long run, hard run, social run, mobility)
- Coaching decisions (what kind of week, how much load, what to adjust)
- Interpreting what the user actually needs from a message
- Anything requiring judgment, nuance, or knowledge

**The test:** is this a fact or a judgment? Facts live in Python. Judgments go to Claude.

**Never:** hardcode content in Python. No fixed coaching rules, no templated session blocks, no if/else synthesis logic. Claude generates all of that.

---

## File naming — non-negotiable

Every file in `src/trellis/` must be exactly one of these categories. Flat structure, no nested folders, no exceptions.

```
# Core — runtime infrastructure
core_assembler.py
core_config.py
core_history.py
core_main.py
core_meta_tool.py
core_onboarding.py
core_oracle.py
core_profile.py        # UserProfile, CurrentContext — global services
core_registry.py
core_router.py
core_summariser.py
core_telegram.py

# Infrastructure — data sources, not domains
infra_garmin.py      # Garmin API client, sync, push, connection management
infra_tracking.py    # health records, readiness scoring, cycle, self-reports
infra_postgres.py    # DB connection + migrate() only — no repo classes here

# Domains — exactly 5 files each
domain_{second_brain|training|learn|music}_models.py
domain_{second_brain|training|learn|music}_service.py
domain_{second_brain|training|learn|music}_claude.py
domain_{second_brain|training|learn|music}_repo.py
domain_{second_brain|training|learn|music}_tool.py

# Migrations
migrations/001_schema.sql   # base schema (fresh installs)
migrations/00N_*.sql        # numbered additive migrations — never wipe live data to avoid one
```

**If a file doesn't fit one of these patterns, it cannot be created.** Stop, explain why it doesn't fit, and get explicit agreement before writing anything. This rule exists because the codebase grew to 58 files by ignoring it.

---

## Domain file pattern

Every domain follows this structure exactly:

| File | Contents | Rule |
|------|----------|------|
| `*_models.py` | Frozen dataclasses only | No I/O, no imports from other trellis modules |
| `*_service.py` | Business logic | Never talks to Claude or DB directly — delegates |
| `*_claude.py` | All Claude calls | Prompts as module-level constants; methods return typed data or None on failure |
| `*_repo.py` | Storage | Protocol at top, Postgres impl below |
| `*_tool.py` | Tool wiring + context loader | Schema dict + handler `(user_id, input_dict, now) → str`; context loader factory at bottom |

---

## The second brain domain

The core of Trellis. Default domain when no other signal fires — if the message doesn't sound like training or structured learning, it lands here.

**What it owns:**
- Brain dumps (raw text in, synthesised + triaged out)
- Ideas (wild, half-formed, philosophical — all valid)
- Tasks and reminders
- Goals (all types — training goals are a subset, not separate)
- Captures (links, quotes, references)
- Periodic cleanup sessions ("what have I got, let's organise it")

**What it does not own:**
- Training plans, sessions, Garmin data → training module
- Structured learning threads → learn module

**The synthesis pipeline** — the feature that makes this a second brain, not a notes dump:

```
user sends brain dump
       ↓
Claude triages + synthesises (domain_second_brain_claude.py)
       ↓
Returns: BrainDumpResult
  - type: idea | task | goal | project_seed | question | reference | mixed
  - cleaned_text: coherent version, not garbled
  - action_items: list[str] — anything that implies a to-do
  - project_hints: list[str] — anything that suggests a larger project
  - raw preserved alongside
       ↓
Stored in DB. Claude responds with the cleaned version + any actions surfaced.
```

The original dump is always preserved. The synthesis sits alongside it.

**Goals live here, not in training.** All goals — race, life, habit, general — are owned by second_brain. Training domain reads training-relevant goals (goal_type: race|aerobic|strength) as Tier 1b context when building plans. Second_brain shows all goals.

---

## Training domain

A module. The old code (`training_*.py`, `ef_*.py`, etc.) stays functional until this domain is built. Build second_brain first.

**What it owns:**
- Weekly training plans and sessions
- Training arc (periodisation)
- Garmin integration (activities, sync, push to watch)
- Readiness adaptation
- Morning check-ins, post-workout logs, strength sessions
- Training-specific anchors (PT, social run)

**What it does not own:**
- Goals — reads them from second_brain's goals table filtered by goal_type
- General tasks — those are second_brain

---

## Learn domain

A module. Deliberate knowledge building — different cognitive mode from second_brain.

**The distinction:** second_brain is retrieval and action (closing tabs, not losing things). Learn is synthesis and understanding (building something deliberately). Test: does it need to be *done* or *understood*? Done → second_brain. Understood → learn.

**What it owns:**
- Learning threads (named topics)
- Entries within threads
- Periodic synthesis within a thread

---

## Music domain

A module for your music and creative practice. Its first and main integration is
the Spotify connection to your own taste; on top of that sit tools that beat the
executive-function wall of *choosing* — surface what to play, build a playlist, or
nudge you to start. Not limited to DJing (the dusty Traktor S4 was just the first
case) or to Spotify — gear like the PO-33 and other instruments are fair game as
knowledge/nudge tools even without an API.

**The distinction:** a hobby/creative surface with its own external integration
(Spotify, like training has Garmin). Its job is collapsing "all of music" down to
a few good options — to play, to practise with, to save, or to learn.

**What it owns:**
- Spotify connection (OAuth, token refresh) + synced library (saved/top/recent/playlists)
- "N tracks for a vibe" — vector-recall shortlist → Claude picks the final few
- Playlist creation — turn a vibe or a set into a real Spotify playlist
- Practice / gear nudges (DJ, Pocket Operators, whatever you're learning)
- Room for more music tools over time (incl. future MCP integrations)

**What it does not own:**
- General tasks/goals/reminders → second_brain
- The meaning-index — tracks file into the shared memory_index, so recall spans them
- Getting audio into Traktor — that's the streaming-proxy on the hardware side

---

## Claude call rules

- Prompts are **module-level constants**, never inline strings inside methods
- Every Claude call has a **typed return** — parse the response, return a dataclass or None
- Parse failures **log a warning and return None** — callers handle gracefully, never crash
- `max_tokens` must be set generously for calls that return full JSON (8192+)
- Never call Claude for something Python can calculate deterministically

---

## Service rules

- Services are **thin** — validate input, call repo/claude, return typed data
- No 500-line if/elif chains — one method per action
- Services never talk to Claude directly — that goes through the `*_claude.py` module
- Fallback on Claude failure: **raise**, don't silently return a degraded result

**Services never return strings.** String formatting belongs exclusively in the tool handler.

```python
# Wrong
def get_week_plan(self, user_id) -> str:
    return "No plan found." if not plan else f"This week: ..."

# Right
def get_week_plan(self, user_id) -> WeekPlan | None:
    return self.repo.latest_active(user_id)
```

The tool handler is the only place strings are assembled.

---

## Tool design rules

Two patterns, chosen by risk profile:

| Pattern | When to use | Why |
|---|---|---|
| **Dispatch read** `{domain}_get(what: ...)` | All reads | Low-stakes. One tool, clear enum. |
| **Specific named write** `save_week_plan`, `complete_task` | All writes | High-stakes. Explicit names prevent confusion. |

**Always-available tools** (regardless of domain routing):
- `brain_dump(text)` — capture anything, immediately; synthesis happens automatically
- `update_context(data)` — current life situation
- `body_log(type: self_report|cycle|period, data)`

**Second brain tools (~8)**
- `second_brain_get(what: tasks|goals|inbox|ideas|reminders|project)` — dispatch read
- `create_task`, `complete_task`, `update_task`
- `set_reminder`, `cancel_reminder`
- `add_goal`, `update_goal`
- `cleanup_session` — periodic review: "what have I got, let's organise it"

**Training tools (~8)** — build second, existing code stays functional in the meantime
- `training_get(what: plan|today|health|arc|anchors|activities)`
- `save_week_plan`, `save_training_arc`, `adapt_session`
- `training_log(type: morning|post_workout|strength, data)`
- `set_anchor`, `remove_anchor`
- `push_to_watch`

**Learn tools (2)**
- `start_learning_thread`, `add_learning_entry`

**Total: ~20 tools.** The ceiling is 30. Above that, something has been added without being questioned.

Note: Garmin sync is **not** a tool. It runs automatically when stale. Claude never decides to sync.

---

## Context strategy

Three tiers. Every piece of data belongs in exactly one.

**The test:**
- Does Claude need the *content* to reason well? → Tier 1
- Does Claude just need to know something exists or how urgent it is? → Tier 2
- Is it detail Claude might need but often won't? → Tier 3

**Tier 1a — Global** (always loaded, every turn):
- **Profile** — who Cat is, physiology, background
- **Life context** — user-managed via `update_context`. "Wedding in 3 weeks." Affects everything.
- **Latest self-report** — today's energy/body/life load if logged

**Tier 1b — Domain-routed** (loaded when domain is active):
- **second_brain:** active task count + recent ideas summary + active goals
- **training:** training-relevant goals (race|aerobic|strength) + detected patterns/insights
- **learn:** active thread names

**Tier 2 — Snapshot** (always loaded, existence and urgency only):
```
Today: Monday 2 July
Tasks: 3 overdue, 2 due today
Readiness: STEADY
Reminders: 1 due at 18:00
```

**Tier 3 — Tool-on-demand:** task content, session details, thread entries, arc, activity history.

The snapshot does not grow. Any new line must pass: *does this tell Claude something exists or how urgent it is?*

---

## Lean constraints (non-negotiable)

- **One Claude call per turn.** Never add a second call. Add tools instead.
- **Minimal pre-loaded context.** The failure mode is loading too much, not too little.
- **Bounded context.** Insights and history enter as summaries. Never pass raw records.
- **Tools as the API surface.** A future UI calls the same tools Telegram does.

---

## Future UI compatibility

1. **Services return typed data, not strings.** Tool handler converts to string for Claude, REST endpoint converts to JSON. Service is indifferent to both.
2. **Everything through the repo layer.** No ad-hoc SQL in handlers.
3. **Tools have two consumers.** Claude now, UI later. Keep the seam clean.

---

## Things we keep re-learning (don't repeat)

- **Don't build what wasn't asked for.**
- **Content belongs to Claude.** Never hardcode session content, coaching rules, or synthesis logic in Python.
- **max_tokens too low truncates JSON silently.** Always set 8192+ for structured responses.
- **ReadinessBand has no MODERATE.** Valid values: LOW, STEADY, READY, STRONG.
- **Don't revert a commit by amending.** Create a new commit.
- **Goals table must be in the reset script.** Causes duplicate goals on re-onboarding.

---

## Build order

1. **second_brain** — this is Trellis. Build it first.
2. **training** — module, add after. Existing code stays functional in the meantime.
3. **learn** — module, comes naturally after second_brain is solid.
4. **music** — module, add after. Its own Spotify integration; rides on the shared memory index.

---

## Deployment

- Runs via Docker Compose: `docker compose up --build`
- `.env` is gitignored — contains live bot token, API key, secrets. Never log or expose.
- DB reset (full wipe): `docker compose down -v && docker compose up --build -d`
- Nightly DB backup: `scripts/backup_db.sh` (launchd, dumps into the vault's `.backups/`)
- Tests: `.venv/bin/pytest tests/ -q`
