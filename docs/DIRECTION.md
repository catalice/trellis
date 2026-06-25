# Trellis — Direction

**Last updated:** June 2026 (rewrite/clean-architecture)

---

## What Trellis Is

A personal operating system. Not a running bot with extra features — a trellis. Chosen structure, built by Cat, for Cat.

A trellis does not grow for you. It gives you something to grow against. It holds shape so you don't have to hold it yourself. It is not a cage — it is chosen structure, and it can be extended, pruned, redirected as you change.

Trellis has capabilities. What it does with them — when things surface, how they're delivered, what your day looks like inside it — adapts to what you actually need. There is no fixed routine. It learns yours, or helps you build one. The structure is yours to decide and Trellis holds it.

It motivates without cheerleading. It does not guilt. It does not ask unnecessary questions. And it does not assume you always know what you want — it can help you figure that out, then help you get there.

---

## Architecture Philosophy

Trellis has two distinct layers with different responsibilities. Mixing them up is how the system becomes rigid or expensive.

**Python: memory, consistency, side-effects.** Data persistence, Garmin sync, context assembly, tool execution, readiness scoring — anything that must produce the same result every time or modify external state. These are facts, calculations, and actions. Python is fast, cheap, auditable, and deterministic. It does not hallucinate.

**Claude: judgment, language, knowledge, adaptation.** Interpreting what the user actually needs, deciding what to do about a fact, coaching responses, flexibility when the situation is novel. Claude holds the knowledge of what a good warm-up is, what high soreness means for today's session, whether a pattern in the data matters. Claude does not store state — it reasons over the state Python hands it.

The failure mode in either direction: Python making judgements that belong to Claude (rigid coaching rules, hardcoded thresholds where nuance is needed) or Claude being used for things Python should own (re-fetching data it was already given, side effects that need to be reliable).

When adding a feature: is this a fact or a judgement? Facts live in Python. Judgements go to Claude.

**Lean by design.** Four constraints that must hold from the start, not added later:

- One Claude call per turn. Never add calls — add tools.
- Domain routing from day one: a lightweight classifier decides which domains are relevant before the oracle loads context. A task question never loads Garmin data. A training question never loads the learning curriculum. This is not an optimisation to add later — it shapes the oracle architecture from the first line.
- Bounded context: insights and patterns enter the oracle as summaries, not raw records. Context window size is a cost and a constraint.
- Tools as the API surface: the UI, when built, consumes the same tools Telegram does. No second layer.

---

## Core Capabilities

These are the things Trellis does, independent of how they're surfaced or structured.

### Garmin Integration
- Pull health data: sleep, HRV, body battery, readiness, HR zones, recovery time
- Pull activity data: recent runs and workouts for review, post-run analysis (pacing, effort vs plan, whether it matched the intent)
- Fitness trends: VO2 max over time, fitness vs fatigue, long-run progression
- Write structured workouts to the device: intervals with exact targets pushed before a session

### Storage and Knowledge
- Persist everything: health data, training history, tasks, ideas, goals, captures, learning progress, insights
- Build knowledge over time — not just data, but conclusions drawn from data

### Scheduling and Reminders
- Training plans that adapt weekly
- Task reminders at the right time
- Configurable routines (morning check-in, end of day, pre-run)

### Read and Write
- Read from external sources (Garmin, eventually others)
- Write back (workouts to Garmin, notes to Obsidian while it's in use, eventually a UI)

---

## The Modules

### Body

Everything physical. The foundation — everything else is calibrated against it.

- **Health**: Garmin sync, readiness scoring, sleep, HRV, body battery, cycle phase, soreness, 3-day trends
- **Training**: continuous coach making weekly decisions based on actual state (readiness, load, cycle, life load). Holds your goals, builds toward them, adjusts when life intervenes. Strength, running, mobility — the full picture. Post-run analysis and feedback.
- **Mobility**: deliberate sessions tailored to what the body needs long-term, not filler

### Mind

Everything cognitive and creative.

- **Tasks**: captures, to-dos, reminders. Organised without turning every thought into an obligation. Patterns noticed — what gets deferred, what gets done, what should never have been a task.
- **Captures**: brain dump and stream-of-thought, synthesised at capture time. Both the raw input and the synthesis are stored — the raw is accessible on request, the synthesis is what surfaces in context and Obsidian. The synthesis is always done by Claude; nothing is stored unsynthesised.
- **Learn**: a curriculum, not a feed. Starting from the beginning and moving forward systematically. You always know where you are in the map and what comes next. Connections between things matter as much as the things themselves. Trellis knows your position, picks up where you left off, learns which angles land for you.

### Intelligence Layer

The engine underneath everything. Reads across all domains — sessions, self-reports, tasks, captures, learning interactions — draws conclusions, and makes those conclusions available to the rest of the system. This is how Trellis gets sharper over time. Not retrained. Pattern synthesis that compounds.

---

## Interfaces

Trellis is currently Telegram-only. The long-term picture:

**Telegram**: conversational, always available, the primary interaction layer. Stays.

**UI / Dashboard**: a visual layer showing everything Trellis tracks — body metrics, training plan and history, tasks, learning progress, insights. Comparisons over time. The place to see the full picture at a glance. Tasks and learning especially benefit from a visual interface.

**Obsidian**: currently used for write-only visibility (tasks, training notes). Useful as a transitional layer. Replaced by the UI when that exists — not a core dependency.

---

## Current State

Built and working:

- Oracle architecture — one AI call per turn, full conversation context, tool loop
- Body / Health: Garmin sync, readiness scoring, self-report, cycle tracking, soreness, 3-day trend, HR zones
- Body / Training: continuous coach (readiness, load, cycle, life load), weekly plan creation, session detail, social run, deload/holiday weeks, training arc, PT logging, session completion matched to Garmin activities
- Mind / Tasks: create, complete, archive, update (priority, energy, due date), completed task history
- Mind / Captures: brain dump synthesised at capture time, stored with raw + synthesis, surfaces in context and Obsidian
- Mind / Reminders: task-linked and standalone, cancel, list scheduled
- Mind / Learn: curriculum engine, thread tracking, position history, entry recording
- Intelligence layer: domain-agnostic pattern engine, daily background scan, active insights in oracle context, snooze/resolve/reject responses
- Goals as first-class data
- Current context: general notes, physical state, cognitive/exec state
- Domain preferences: per-domain preferences stored in DB (`user_preferences` table), loaded only when that domain routes — user states preferences conversationally, Trellis saves and applies them. Covers training, learn, ef, notes.

Not yet built:

- Post-run analysis and Garmin workout writing
- Body / Mobility as deliberate pillar
- Adaptive routines (configurable check-in timing and format)
- UI / Dashboard
- Obsidian two-way sync (low priority given UI direction)

---

## Build Order

1. ~~Continuous coach~~ ✓
2. ~~Pattern learning foundation~~ ✓
3. ~~Goals as data~~ ✓
4. ~~Session completion~~ ✓
5. **Post-run analysis** — surface recent activity detail, compare against plan, motivate with honest feedback
6. ~~**Learn module**~~ ✓
7. **Mobility pillar** — deliberate mobility in training, not filler
8. **Adaptive routines** — configurable check-in timing and format; Trellis learns or is told when and how to surface things
9. **UI / Dashboard** — visual layer for training, body, tasks, learning, insights
10. **`trellis-setup` wizard** — single CLI command for first-time setup: creates DB, runs all migrations, connects Garmin, sets Telegram bot token, triggers onboarding. A complete beginner should be able to go from zero to running in one command.
