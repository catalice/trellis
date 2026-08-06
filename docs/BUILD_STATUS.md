# Trellis Build Status

**Last updated:** 5 August 2026 (post-audit)
**State:** LIVE — in daily use over Telegram since July 2026.

What exists in code NOW. The June 2026 planning docs live in `_archive/` —
they describe a design (Claude-classifier routing, deterministic training
engine, readiness calculator) that was superseded by the build; read them as
history, not truth. Working rules: `../CLAUDE.md`. Direction: `DIRECTION.md`.

## The live system

**Big brain + houses/rooms** (see CLAUDE.md for the full model):

- **One oracle turn per message** (`core_oracle`): agentic loop, all 21 tools
  always available, semantic routing shapes context only. Silent end_turn
  after tools gets ONE nudge follow-up call; a deterministic tool-result
  fallback remains as backstop. Model: `claude-sonnet-5` (via `ANTHROPIC_MODEL`).
- **Semantic router** (`infra_router`): local bge-small embeddings, houses
  scored by best-matching room, floor 0.635, generic chat → big brain.
  Keyword router is the degraded-mode fallback. Proof harness:
  `tests/test_semantic_router.py`.
- **Focus** — tasks/seeds, reminders, goals, captures, efforts, brain-dump
  synthesis (the one extra Claude call), cleanup sessions, web search.
- **Sense** — state logs (energy/mood, felt_at), meds/sleep/period events,
  cycle day, and the READ of synced Garmin health (sleep, HRV, body battery,
  RHR — raw signals with loud staleness marking; there is NO computed
  readiness score or band).
- **Move** — continuous running coach: goal from Focus, plan JSON authored by
  Claude, real-dates calendar from Python, Garmin push/read/sync, run log
  deduped by Garmin activity id. The week is authored in the Sunday review
  (reminder-driven); a stored week left in the past is flagged for
  review + re-author before anything else.
- **Memory** — Trellis-wide semantic index (embed-on-write, `recall` tool),
  local embedder baked into the Docker image.
- **Obsidian projection** (`infra_obsidian`): daily capture notes (append-only,
  user frontmatter preserved), Tasks/Seeds/Tracking views, training plan +
  week archives, effort pages.
- **Telegram** (`core_telegram`): text + voice (Groq whisper), per-user turn
  serialisation, reminder delivery loop (15s), background Garmin sync (6h).
- **Preferences**: global loads every turn, per-house with routing;
  saves APPEND by default (replace is explicit).
- **History**: 10-turn context window, Groq summarisation every ~20 turns with
  a DB-persisted cursor, transcript pruned to 500 turns after summarisation.

## Not built / next

- **The Watcher** (intelligence layer) — spec settled, not built.
  See `DIRECTION.md` and the memory notes; assembler socket waiting.
- **Learn house** — future.
- Public-repo scrub before any re-publishing (repo is private).

## Operations

- Deploy: `docker compose up --build` (postgres + health-worker + bot).
- Migrations: `src/trellis/migrations/001–012`, applied automatically on start.
- Nightly DB backup: `scripts/backup_db.sh` → vault `.backups/`.
- Tests: `.venv/bin/pytest tests/ -q`.
