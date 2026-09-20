# Trellis Build Status

**Last updated:** 20 September 2026
**State:** LIVE — in daily use over Telegram since July 2026.

What exists in code now. Working rules: `../CLAUDE.md`. Direction: `DIRECTION.md`. Setup: `SETUP.md`.

## The live system

- **One oracle turn per message** (`core_oracle`): agentic loop, 18 tools always available. A silent turn after tools gets one nudge call.
- **Semantic router** (`infra_router`): local embeddings, houses scored by best-matching room, generic chat goes to the big brain. Routing shapes context only, never tools.
- **Focus** — tasks, seeds, reminders and check-ins, goals, captures, efforts, brain-dump synthesis, web search (web, news, PubMed, scholar, trials).
- **Sense** — one tracking log (their words + a facts map; a new kind is data, not schema), cycle maths, Garmin health with loud staleness marking. No computed readiness score.
- **Move** — running coach: plan authored by Claude, dates from Python, the watch's activity log is the record, Garmin push/read/sync.
- **Learn** — knowledge maps the user draws; sources fetched, never recalled; retrieval tests.
- **The Watcher** (`core_watcher`) — weekly discovery by Claude, verification in Python, verdicts stored.
- **Memory** — semantic index with `recall`; local embedder baked into the image.
- **Life context** — a short dated log, one line per entry, each lapsing on its own.
- **Preferences** — one rule per row. Global rows load every turn, house rows with their house.
- **Shape control** — what the model writes into always-loaded slots is limited to 10 words by Python; near-repeats are named.
- **History** — the last 24 hours verbatim (capped), plus dated past-tense summaries per house.
- **Computed lines** — countdowns, cycle, and what was logged in the last 30 days come from Python, so they cannot go stale.
- **Obsidian projection** (`infra_obsidian`) — daily notes, tasks, tracking, training plan, maps, and the Brain pages (profile, context, preferences).
- **Telegram** (`core_telegram`) — text and voice, per-user turn lock, reminder loop, background Garmin sync.

## Operations

- Deploy: `docker compose up --build` (postgres + health-worker + bot).
- Migrations: `src/trellis/migrations/001–026`, applied on start.
- Nightly DB backup: `scripts/backup_db.sh` → vault `.backups/`.
- Tests: `.venv/bin/pytest tests/ -q`.
- Before a push: `scripts/check_public_hygiene.sh` (also the pre-push hook).


## Queued next (agreed 30 Aug 2026)

**Paused:** `RELIABILITY_PLAN.md` supersedes this queue while it runs.

Builds, in order:
1. Document ingestion — chunking (heading-aware + sentence boundaries), PDF/text
   extract, chunk identity in the memory index, Telegram file handler; pieces land
   on Learn maps. Needs an awake spec (migration design).
2. News, layer two — the user's own RSS source list (stored as preference, read
   directly — no aggregator service), "brief me" onto map scaffolding, Guardian
   full-text ingest.
3. Two-channel Watcher — imported-from-literature hypotheses, provenance-labeled
   ("discovered" vs "imported"), same Python verification. First: verify the
   planting write-path exists at all.
4. Watcher-proposed trackable kinds — recurring words in their notes become
   proposed tracking kinds; their yes mints the kind. Extend daily frames to
   correlate any kind by name.
5. Global tool fold — at trigger (clean dispatch-write logs); the
   context/preferences meta pair rides the same review.
6. From-scratch setup test — follow SETUP.md LITERALLY on a clean environment
   (the doc is the test script; every divergence fixes the doc or the code),
   ending with onboarding building a Trellis for whoever showed up.

Standing doc rule (4 Sep): the public docs speak to STRANGERS.
README/SETUP/BUILD_STATUS carry no insider shorthand - every release that
changes behaviour updates them in plain words, same discipline as tests.

## Live watch (constitution trim, 20 Sep 2026)

Not faults. Signs to watch for in live use:
- Streaks, clock-time schedules or option menus return → the design laws carry less weight as preference rows.
- A search on every passing health remark → the "fetch a source" line is too broad.
- A tool call made, the question left unanswered → the merged Listening lines.

Small, awaiting a yes: Watcher in-chat evidence labeled as historical examples
(the hallucinated-effort fix); NYT news
source if wanted.
