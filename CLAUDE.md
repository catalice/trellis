# CLAUDE.md — Working rules for Trellis

Read this before touching anything. These rules exist because we learned them the hard way.

---

## What Trellis is

Trellis is a second brain. Not a coaching bot. Not a task manager. A persistent external mind that holds what Cat's brain can't — ideas, tasks, goals, things worth remembering — and synthesises it into something useful.

**The shape: a big brain with houses.**

- **The big brain** is the orchestrator plus the core — always on, every turn. It holds who Cat is (profile), what's live in her life (current context), and the snapshots. Generic chat and anything that isn't clearly about a specialist area lands here, by design. There is never a "routing failure" — worst case you're home in the big brain.
- **Houses** are specialist domains that light up only when a message means them:
  - **Move** — the running coach. The DOING side of the body: plans, workouts, races, Garmin push.
  - **Sense** — health and wellbeing tracking. The MONITORING side: mood, energy, meds, cycle, sleep, HRV, readiness. Owns the Garmin health data.
  - **Focus** — executive function. The recording side: tasks, reminders, ideas, brain dumps, goals, efforts.
  - **Learn** — deliberate understanding. Knowledge maps the user draws, built bottom-up; sources fetched, never recalled.
- **Rooms** are the phrases inside each house that describe what it handles ("shopping lists", "recovery and readiness", "intervals and long runs"). The router matches messages against rooms, not house names. Growing a house = adding a room to its list.

The axis between Move and Sense is doing vs monitoring, NOT physical vs mental — that's why aches live in Sense.

Full direction: `docs/DIRECTION.md`.

---

## The one rule that matters most

**Ask before building.** If the user didn't explicitly ask for a feature, don't build it.
Explaining a problem is not a request to solve it. Confirming something works is not a request to extend it.
When in doubt: say what you'd do and ask first.

---

## Build public — the repo is machinery, the person lives in the brain

This repo is public. Everything in it must be buildable, readable, and usable by a
stranger — which is only possible because of a hard line (the user's design, Aug 2026):

**Anything we build should be public. Nothing personal is ever hardcoded.**
Medication names, employers, cities, friends' names, gendered pronouns for the user,
life events — none of it belongs in code, prompts, tool descriptions, tests, or docs.
The person is not in the repo: the person lives in the Trellis brain — profile,
preferences, memory, tracking, vault — **built by the user, with use.** Onboarding
exists so Trellis meets a stranger fresh; preferences exist so it learns how to talk
to them; the memory index exists so it learns what matters to them. If a feature
seems to need personal knowledge, it must LEARN it at runtime, never ship it.

Corollaries:
- Prompts and tool text say "the user / they / their" — never a name, never a gender.
- Examples in prompts and tests are generic ("confirm the electrician is coming"),
  never real people, meds, or places from anyone's life.
- Personal data lives only in the DB, the vault, and `.env` — all outside git.
- `scripts/check_public_hygiene.sh` is the mechanical backstop: it greps the tree
  for a personal-marker denylist and the pre-push hook blocks the push if it hits.
  The denylist itself lives in `.hygiene-denylist` (gitignored — the markers ARE
  personal data).
- Before any push that adds prompts, tools, tests, or docs: re-read this section
  and check the change against it. The scrub of Aug 2026 exists because this rule
  didn't — never again.

---

## Source in truth — nothing asserted without one

The user's law (30 Aug 2026): **everything Trellis asserts must trace to a
source** — their data (tracking rows, activity records, stored plans), their
words (captures, notes, annotations), or a citation fetched live and saved.
Never model recall dressed as fact. This is why retrieve-before-assert exists
(read the stored thing before describing it), why the Watcher verifies in
Python before speaking, and why any future knowledge feature (Learn) must
fetch and store its citations — a reference that can't be followed back to
its source doesn't get kept.

---

## Routing — how a message reaches a house

Routing is SEMANTIC (meaning, not keywords) and shapes CONTEXT ONLY. Tools are always available regardless of routing — Claude decides what to call from tool descriptions. This is non-negotiable: gating tools by routing caused the model to deny real capabilities.

The mechanism (`infra_router.SemanticRouter`):
1. Each house declares its rooms (`FOCUS_ROOMS` / `SENSE_ROOMS` / `MOVE_ROOMS` in its `*_tool.py`). Rooms are embedded once at startup (local fastembed/bge-small — no network, no key, baked into the Docker image).
2. Per message: one cheap local embedding, then each house scores by its BEST-matching room (max cosine). Best-room, never a blended description — a single door-label vector dilutes sharp signals.
3. The clearly-best house is routed; a second house joins only if within the close margin (ambiguous → load both — Claude self-resolves mid-turn, agentic loop). Below the floor → route EMPTY: the big brain carries the turn.
4. Embedder down → keyword fallback (`core_router.Router`, with focus as default in that degraded mode only). Routing must never take the bot down.

**Guess, don't ask.** When ambiguous, load both candidate houses and proceed. Asking "A or B?" costs 2 turns every time; guessing costs 2 only on a miss. Ask only when genuinely ~50/50 AND a wrong guess is costly/hard to undo (pushing the wrong workout to the watch, deleting something).

**Writing rooms — learned the hard way:**
- Short, concrete, 2+ word phrases. Single words are noise magnets.
- NO time words ("today", "daily", "morning") — they drag in scheduling and greeting messages.
- NO conversational phrasing ("how am I doing") — it attracts all everyday chat.
- NO catch-all claims ("anything else lands here") — the big brain is the catch-all, not a house.
- One question shape can get its own room ('recovery and readiness' AND 'readiness score' both exist — max-over-rooms means each message finds its best magnet).
- Tuning is ADDITIVE: diagnose with the per-room score dump, fix by adding a room, not rewording a sentence.

**The proof harness is the build gate**: `tests/test_semantic_router.py` prints the real score table (`-q -s`) and locks clear cases + generic-chat-routes-empty as permanent regressions. Any room or threshold change must keep it green. A live misroute becomes a one-line room addition plus a battery case.

The router logs the winning room per turn (debug) — misroutes are self-explaining.

---

## Python / Claude boundary

**Python owns:**
- Data persistence (all DB writes)
- Garmin sync and health data
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
# Core — runtime infrastructure (the big brain's plumbing)
core_assembler.py    # one turn end to end: routing, context tiers, tool binding
core_config.py
core_history.py
core_main.py         # the ONE place houses are registered and wired
core_meta_tool.py    # always-on tools: update_current_context, save_preferences
core_onboarding.py
core_oracle.py       # the agentic loop (one Claude call per turn, tools until end_turn)
core_profile.py      # UserProfile, CurrentContext — global services
core_registry.py     # houses register here: context loader + tools + rooms + signals
core_router.py       # keyword fallback router (degraded mode only)
core_summariser.py
core_telegram.py
core_watcher.py      # the slow mind: discovery (Claude, weekly) + verification (Python) + patterns table

# Infrastructure — data sources and engines, not houses
infra_embeddings.py  # local embedder (fastembed/bge-small) — memory + routing share it
infra_garmin.py      # Garmin API client, sync, push, connection management
infra_memory.py      # the Trellis-wide meaning index (embed-on-write, recall)
infra_obsidian.py    # vault projection
infra_postgres.py    # DB connection + migrate() only — no repo classes here
infra_router.py      # SemanticRouter — houses scored by best-matching room
infra_search.py      # web search (Tavily)
infra_tracking.py    # synced Garmin health records + sync-run bookkeeping

# Houses — exactly 5 files each
domain_{move|sense|focus|learn}_models.py
domain_{move|sense|focus|learn}_service.py
domain_{move|sense|focus|learn}_claude.py
domain_{move|sense|focus|learn}_repo.py
domain_{move|sense|focus|learn}_tool.py

# Migrations
migrations/001_schema.sql   # base schema (fresh installs)
migrations/00N_*.sql        # numbered additive migrations — never wipe live data to avoid one
```

**If a file doesn't fit one of these patterns, it cannot be created.** Stop, explain why it doesn't fit, and get explicit agreement before writing anything. This rule exists because the codebase grew to 58 files by ignoring it.

---

## House file pattern

Every house follows this structure exactly:

| File | Contents | Rule |
|------|----------|------|
| `*_models.py` | Frozen dataclasses only | No I/O, no imports from other trellis modules |
| `*_service.py` | Business logic | Never talks to Claude or DB directly — delegates |
| `*_claude.py` | All Claude calls | Prompts as module-level constants; methods return typed data or None on failure |
| `*_repo.py` | Storage | Protocol at top, Postgres impl below |
| `*_tool.py` | Tool wiring + context loader + rooms | Schema dict + handler `(user_id, input_dict, now) → str`; context loader factory; `*_ROOMS` + `*_SIGNALS` at the bottom |

---

## The Focus house

Executive function — the recording house.

**What it owns:**
- Brain dumps (raw text in, synthesised + triaged out)
- Ideas (wild, half-formed, philosophical — all valid)
- Tasks and reminders
- Goals (ALL types — race, life, habit. Training goals are a subset, not separate)
- Captures (links, quotes, references) and Efforts (project pages built up over time)
- Periodic cleanup sessions ("what have I got, let's organise it") — the inbox
  via `focus_get`, effort suggestions from the oracle's own judgment in-turn, and
  filing via `save_to_effort(capture_id=…)`. No dedicated tool; ignored captures
  age out of the inbox by themselves after 30 days.

**The synthesis pipeline** — the feature that makes this a second brain, not a notes dump:

```
user sends brain dump
       ↓
Claude triages + synthesises (domain_focus_claude.py)
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

**Goals live here.** Move reads training-relevant goals (goal_type: race|aerobic|strength) cross-cutting when planning. Focus shows all goals.

---

## The Sense house

Health and wellbeing tracking — the monitoring house (Mind).

**What it owns:**
- Self-reported state: mood, energy, meds, sleep, period/cycle (`log_state`)
- **The ONE tracking log** (migration 020, her design): a row = when + their words +
  a FACTS map. A new trackable kind (anxiety, cramps, restless_legs...) is a new key
  — data, never schema. The old state/event shapes are repo-composed VIEWS over the
  log (the logbook pattern); tracked kinds ride Sense context so the model reuses
  names instead of minting synonyms. The Watcher can correlate any kind by name.
- Garmin health data: sleep score, HRV, body battery, resting heart rate — Sense OWNS this; Move borrows it
- The wellbeing snapshot: raw signals with loud staleness marking — there is NO computed readiness score or band; Claude reads the numbers and judges

**Why health lives here and not in Move:** the axis is doing (Move) vs monitoring (Sense). Health data was once mis-filed in the coach — that's why "what's my readiness" mis-routed. Re-homed; don't move it back.

---

## The Move house

The running coach — a lean module (Claude + tools; coaching happens in the turn).

**What it owns:**
- Weekly training plans and sessions (`save_training_plan`, `move_get`)
- Garmin activities: push workouts to watch, read recent workouts, on-demand sync
- Coaching judgment: what kind of week, how much load — Claude's, never hardcoded

**The logbook (migration 017):** the watch's record IS the record. There is no
separate runs table — `garmin_activities` holds every sport, and the user's words
live in its `user_note` column (`update_workout` appends there). Sync's upsert
never names that column, so a resync structurally cannot touch their words. Runs
are a filtered view (`recent_runs`) for baseline math; reviews read every sport
(`recent_workouts`).

**What it reads cross-cutting (never owns):**
- Goals — from Focus's goals table, filtered by goal_type
- Health/readiness — from Sense, to factor into planning

Garmin sync runs two ways: automatically in the background daily, and via the `sync_garmin` tool when Claude judges data is stale for the question at hand.

---

## The Learn house

Deliberate understanding — a different cognitive mode from Focus (registered 30 Aug 2026).

**The distinction:** Focus is retrieval and action (closing tabs, not losing things). Learn is synthesis and understanding (building something deliberately). Test: does it need to be *done* or *understood*? Done → Focus. Understood → Learn.

**The shape: a MAP the user draws.** They are the cartographer — they name regions and place pieces; Trellis is the surveyor: checks the map against fetched sources, keeps "you are here" current, and runs retrieval-practice tests FROM their map (conversation, no tools — outcomes land as kind='test' entries). Rooms cover threads/maps, bottom-up explanation, news-onto-scaffolding, cited research, and quizzes — news and research are tempos of understanding, not separate houses. Source-in-truth is enforced in the service: a kind='source' entry without a fetched URL is refused. Collecting isn't climbing: thread health is when they last climbed, never how much is saved. The Watcher never reads map content; learning ACTIVITY is garden-visible like any behavior. Maps project to `Atlas/Maps/<thread>.md`.

---

## Claude call rules

- Prompts are **module-level constants**, never inline strings inside methods
- Every Claude call has a **typed return** — parse the response, return a dataclass or None
- Parse failures **log a warning and return None** — callers handle gracefully, never crash
- `max_tokens` must be set generously for calls that return full JSON (8192+)
- **Guidance lines are short.** One rule, one or two sentences, plain. Long lines burn tokens and blur — if a rule needs a paragraph, it's two rules or it's unclear.
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
| **Dispatch read** `{house}_get(what: ...)` | All reads | Low-stakes. One tool, clear enum. |
| **Dispatch write** `{house}_add` / `{house}_update` | Create/change verbs repeated across a house's entities | One door per verb (her call, 30 Aug — "we can always regress"). Detail lives in PER-FIELD descriptions tagged by entity, never one prose wall. The proven per-entity handlers stay behind the dispatch. log_state was the precedent: a union write that works. |
| **Specific named write** `save_training_plan`, `push_to_watch`, `delete_entry` | Distinct acts and destructive acts | An act with its own risk profile keeps its own name — deletion must never be reachable by enum typo. |

**All tools are always available** — routing never gates them. The current 18:

- **Always-on / big brain:** `brain_dump`, `recall`, `update_current_context`, `save_preferences`, `pattern_response`
- **Focus:** `focus_get`, `focus_add`, `focus_update`, `delete_entry`, `web_search`
- **Sense:** `log_state` (the ONE tool — reads are context, not tools)
- **Learn:** `learn_get`, `learn_add` (dispatch pair; retrieval tests are conversation + one write)
- **Move:** `move_get`, `save_training_plan`, `push_to_watch`, `sync_garmin`, `update_workout`

(Onboarding mode additionally wires `save_identity`.)

**The fold ladder (decision recorded 30 Aug 2026, her call):** per-entity tools →
per-house verbs (`focus_add`, `learn_add` — where we are) → one global `add`/`get`/
`update`. The last rung is NOT taken yet, deliberately: a global verb unions every
house's fields in one schema, and we fold only on evidence. **Trigger: after ~2
weeks of live dispatch-write use with no wrong-field/wrong-entity errors in the
logs, propose the global fold.** If the middle rung misbehaves instead, fix or
regress it first. Candidate riding the same review: `update_current_context` +
`save_preferences` → one meta pair.

**The tool count must not grow — ideally shrink.** The ceiling is 30; we hold at 18 (Learn born 30 Aug at exactly its funnel-law budget of two. dispatch writes collapsed Focus 30 Aug: create/update x task/goal/reminder/effort folded into focus_add/focus_update. cleanup_session retired 30 Aug — its inbox was focus_get's, its suggestions were the oracle's own judgment wearing a June-era sub-brain, its assign folded into save_to_effort. update_run added 9 Aug, renamed update_workout 30 Aug with the one-logbook restructure — their account of any workout lands on its activity; pattern_response added 9 Aug with the Watcher — her verdicts on patterns must persist. Both flagged, both her call. complete_task folded into update_task status='done' 12 Aug — her call, the shrink direction working). A restructure ADDS ZERO tools — it redistributes. If a change tempts a new tool, flag it and default to NOT adding it. Less is more.

---

## Context strategy

Three tiers. Every piece of data belongs in exactly one.

**The test:**
- Does Claude need the *content* to reason well? → Tier 1
- Does Claude just need to know something exists or how urgent it is? → Tier 2
- Is it detail Claude might need but often won't? → Tier 3

**Tier 1a — The core (big brain, always loaded, every turn):**
- **Profile** — who Cat is, physiology, background
- **Life context** — user-managed via `update_current_context`. "Big deadline in 3 weeks." Affects everything.
- The core is deliberately minimal. The test for core membership: *needed on EVERY call?* If not → it's a house.

**Tier 1b — House-routed** (loaded when the house lights up):
- **Focus:** active task count + recent ideas summary + active goals
- **Sense:** recent state logs + latest health/readiness detail
- **Move:** training-relevant goals + current plan context

**Tier 2 — Snapshot** (always loaded, existence and urgency only):
```
Today: Monday 2 July
Tasks: 3 overdue, 2 due today
Readiness: sleep 82 (7.4h), HRV 55, body battery 71
Reminders: 1 due at 18:00
```

**Tier 3 — Tool-on-demand:** task content, session details, plan detail, activity history, recall.

The snapshot does not grow. Any new line must pass: *does this tell Claude something exists or how urgent it is?* Cost scales with complexity (the green property): chat = big brain only (cheapest); one house = big brain + that house; multi-house = rare. **Know cheaply, fetch precisely.**

---

## Lean constraints (non-negotiable)

- **One ORACLE call per turn** (the agentic loop). Two bounded single-shot guards are the only exceptions, both born from live failures: the silent-turn NUDGE (empty reply after tools) and the ANSWER CHECK (tool-turn + question in her message -> tiny standalone review call, item 29). Never add unbounded extra calls; add tools instead. (Embeddings are not Claude calls — local and cheap.)
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
- **max_tokens too low truncates JSON silently.** Always set 16000+ for structured responses (on Sonnet 5, thinking shares the max_tokens cap).
- **Preferences accumulate.** save_preferences APPENDS by default; replace=true only for deliberate rewrites — a new preference must never silently erase an old one.
- **Exactness ≠ permanence.** One exact prescription per session, but every prescription is re-chosen in the Sunday weekly review — never carried forward by default.
- **Don't revert a commit by amending.** Create a new commit.
- **Goals table must be in the reset script.** Causes duplicate goals on re-onboarding.
- **Never gate tools by routing.** Routing shapes context only.
- **Room phrases: no single words, no time words, no chat phrasing, no catch-alls.** See Routing.
- **Design for the actual brain.** Anchors not schedules, dials not switches, experiments not streaks, one-action protocols, they-are-the-study — the runtime laws live in the constitution (`_SYSTEM_BASE`, "What you ask of them"); every feature must be designed so those laws are followable.
- **A piped pytest lies.** `pytest | tail` masks the exit code - twice now a red suite rode a green-looking pipe to deploy. `set -o pipefail` or check the code separately, every time.
- **Clean-audit every stage of a restructure**, not just the end: 0 non-conforming filenames, 0 dead modules, 0 duplicated logic, tests green + entrypoints import, tool count flat.

---

## Deployment

- Runs via Docker Compose: `docker compose up --build`
- `.env` is gitignored — contains live bot token, API key, secrets. Never log or expose.
- DB reset (full wipe): `docker compose down -v && docker compose up --build -d`
- Nightly DB backup: `scripts/backup_db.sh` (launchd, dumps into the vault's `.backups/`)
- Embedding backfill (safe to re-run): `uv run python scripts/backfill_embeddings.py`
- Tests: `.venv/bin/pytest tests/ -q`
- The embedding model is baked into the image (Dockerfile) — the bot embeds offline at runtime.
