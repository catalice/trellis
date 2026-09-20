# Reliability plan

**Status:** stage 1 built, reviewed three times, merged and deployed 20 September 2026.
Stages 2–3 are next, on their own branch.
Finding numbers (F1–F21) refer to the external review of revision `954d93f`.

## Goal

The user stops supervising Trellis: no checking whether "done" meant done, no
correcting its memory, no re-asking the half of a message it missed.

## Measure

Relative to use, never a raw count — fewer corrections can also mean less trust.

- Requests completed without correction or repetition, as a share of requests.
- Interventions per interaction.
- Serious failures (lost data, false "done", wrong record) counted apart from wording.
- Usage volume beside all three. Falling use is a warning, not a win.

Source: conversation history, tagged after the fact by whoever is doing the work.
The user keeps no tally. Baseline: the last two weeks of history. Known blind
spot: anything noticed silently, checked elsewhere, or abandoned.

## Rules

- No unrelated feature expansion. Capabilities that repair an existing promise
  (full retrieval, real source content) are in scope.
- One coherent, tested change per deploy.
- Failure cases are proven by controlled tests, not by waiting for real use.
  Real use judges whether the experience improved. The user is not the tester.
- A correction the user keeps making is a bug in the path, never a new preference.
- No stage is blocked by a decision it doesn't depend on.
- While this plan runs it supersedes the feature queue in `BUILD_STATUS.md`.

## Sequence

### 1. Protect data and the boundary

These are on active paths now.

- Backup: publish atomically (a failed retry must not replace a good dump), safe
  rotation, a documented and tested restore, including the key for stored Garmin
  sessions (F18). Backups cover the vault's hand-written content as well as the
  database and the key. Confirm whether an off-machine copy exists.
- Garmin: a field missing from a partial sync never overwrites a stored reading;
  partial success is reported as partial (F6).
- Vault: never delete or overwrite hand-written content; check destinations
  before rename; stable file identity, not sanitised titles (F4).
- Activity data: splits survive the storage boundary; a segment is dropped as a
  container on structure, never on relative duration (F14).
- Migrations: verify preconditions, reconcile every row, abort on the unmatched (F5).
- Boundary: database reachable from the host only, no fixed public credentials;
  private chats only; an empty allowlist admits nobody (F2).
- Personal material out of tracked files: synthetic fixtures and narratives;
  check the commits being pushed, not only the tip (F3).

Done when: each failure above is reproduced in a test, then can't be.

### 2. Regression cases and the model boundary

- One set of scenarios, simulated services and outcome checks, run two ways:
  with a **scripted model** (deterministic tests — prove what the software
  guarantees) and with a **real model** (evaluations — show how well a model
  behaves). Different evidence, shared infrastructure.
- Scenarios: missing data, failed sends, ambiguous activities, corrections,
  several requests in one message. Drawn from real failures, anonymised, with
  representative payloads that pass through the storage boundary.
- The scripted model needs a seam, so the model boundary starts here — see
  *Model boundary* below. Current behaviour is preserved through the connector
  first; faulty behaviour changes afterwards, in separate reviewable steps.
- Each reproducer is written immediately before its fix, not all up front.
- Existing tests that lock in a fault are rewritten to assert the user's outcome.

Done when: the harness runs both ways, and the conversation engine runs against
the scripted model.

### 3. Truthful outcomes

An execution design, not another instruction to the model. Separating
components does not by itself fix a false "done".

- A durable action record, independent of the model, so an interrupted response
  doesn't erase knowledge of what happened.
- Outcomes: succeeded, partial, failed, **unknown**. A timeout can mean the
  action happened and the acknowledgement was lost — never retried blindly.
- The final reply is assembled against the recorded outcomes. An earlier success
  claim cannot ship after the action failed (F10); uncertainty is stated, not
  smoothed over.
- Reminders: claimed, executed and accepted-by-Telegram are separate states —
  the API confirms a message was sent, not that it was read, so the state is
  named for what it shows. Retrying a send never re-runs the actions that
  produced the message. Crash-recovery tests sit around each side effect (F9).
- Workout push: a dated session is its own identity; the previous state
  survives until the new one is confirmed (F7). Notes attach to an explicit
  activity, or wait for it (F11).
- Erase: does what the user decided it means, and says exactly what remains (F12).
- Recurring reminders keep local wall time across clock changes (F19).
  **Date-bound: before 25 October 2026 — may jump the queue.**

### 4. Retrieval, memory, answer completeness

- Authority across stores: the database is authoritative; the index and
  generated vault pages are rebuildable from it; hand-written vault content is
  independently authoritative and preserved; summaries are invalidated or
  repaired. Each multi-store update says what happens when one store fails.
- Full record retrieval by identity, paginated; fragments are for navigation.
  Absence is reported with its scope (F15).
- One indexing lifecycle and a reconciliation pass: missing, stale, null,
  orphaned (F16).
- Continuity: decide what must outlive the verbatim window, then carry it
  forward or store it before pruning. Summaries must not overwrite unresolved
  threads (F17).
- Corrections amend the record, its projections and its index, and retire the
  wrong version.
- Backdated entries land on one day (F13). Extra tracked kinds reach the daily
  view and the Watcher.
- House preferences stay loaded when the reply to a proposal is just "yes".
- Unanswered parts of a message. An earlier checking step degraded replies, so:
  measure first, then trial in the harness before anything ships.

Done when (answer completeness): multi-part cases in the harness get every part
answered, with no loss of reply quality on single-part cases.
Done when (memory repair): a rename, a delete and a correction each leave the
database, index, vault and summaries in agreement.

### 5. Evidence

- Health and science explanations rest on retrieved source content — not
  titles, snippets or a synthesised answer (F1). Abstracts help; the
  qualification that matters is sometimes in the results or methods.
- Replies can tell apart: observed, reported by the user, supported by a
  source, inferred.
- A disputed explanation triggers a recheck, not a second plausible explanation.
- The Watcher tests direction, keeps missing data as unknown, and keeps
  medication identity (F8).

### 6. One owned responsibility — trial

Only after the measure has moved. Candidate: preparing and following through
the training week. Agreed once: what it may do alone, what needs a decision,
when it revisits. Unfinished business is a durable record, not a summary.
Accepted when the user gives ordinary life updates and makes the real
decisions, with no procedural coaching.

### Model boundary (built across stages 2–3)

| Component | Responsibility |
|---|---|
| Context and prompts | Assemble knowledge, preferences, instructions. |
| Conversation engine | Run the turn, request tools, handle unfinished work, prepare the reply. |
| Model connector | Translate for one provider; own its state, caching, errors. |
| Tool execution | Validate, enforce permissions, execute, record structured outcomes. |
| Domain services | Own tasks, training, tracking and their storage rules. |

- An action means the same whichever model asked for it.
- Models are chosen in configuration, background jobs included; credentials are
  required only for the providers selected.
- Provider features stay inside the connector — no lowest-common-denominator
  text interface.
- A second live provider is deferred. The scripted model proves separation, not
  portability: the result is *prepared for alternative providers*, not
  *interchangeable*.

Accepted when: conversation and domain logic run against a scripted model
without importing a provider client or building provider-specific messages; the
current provider stays live; action outcomes survive a failed model response;
delivery retries do not repeat completed actions.

### Alongside

Setup documents that match the code (F21); onboarding that can be skipped and a
profile that can be corrected later (F20); stale templates and direction docs
marked historical.

## Review breaks

The planned handover points. Each build batch is implemented and tested, then
pauses for independent review **before deployment**. Review findings are
resolved before deploying.

1. After stage 1 — data protection and access restrictions complete.
2. After stages 2–3 together — regression harness, model boundary and truthful
   outcomes complete.
3. After stage 4 — retrieval, memory and answer completeness complete.
4. After stage 5 — evidence and Watcher changes complete.
5. Before stage 6 — a readiness review before the ownership trial starts.

Within a batch, changes stay coherent and separately reviewable, and one
coherent change per deployment still applies. There are no mandatory review
stops between individual fixes.

## Follow-ups carried forward

- **Backup lock:** the third review noted a remaining race in lock reclamation.
  It does not corrupt a published dump and did not block the stage 1 release;
  close it in a later batch.
- **Off-machine backup: none exists yet.** The vault (with its hidden
  `.backups`) and `.env` live on one machine. Deferred by the user's decision at
  the stage 1 deploy; until it's arranged, losing the machine loses everything.
- **Shared effort pages** from older installs are protected from moves and
  removal but not yet separated.
- **Pure projections** (Brain pages, task and tracking views, the training plan
  page) are still rewritten whole; whether any of them hold hand-written content
  is decision 5 below.

## Decisions for the user

Only what needs a preference. None blocks stages 1–2.

1. What "erase" removes: database, index, vault text, conversation history, backups.
2. Health boundary: record and explain, or also recommend.
3. Reminder failure: a possible duplicate or a possible miss — answered
   separately for notifications and for check-in actions.
4. Whether an explicit instruction is itself the authorisation, with
   confirmation kept for Trellis's own proposals.
5. Which vault pages hold hand-written content, and which are disposable projections.

## Engineering decisions (stated, open to challenge)

- One person per instance, enforced.
- A workout's identity is the dated session, never its name.
- `CLAUDE.md` and `BUILD_STATUS.md` say what the system is; older direction
  documents are historical. Their engineering rules — files per house, tool
  count, token budgets — are revisable on evidence. Fewer tools can mean more
  complicated tools.

## Not yet estimable

Structured outcomes, delivery recovery, and erasure across stores contain
unresolved design work. No sitting count is committed.

---

## Design hypothesis: handwriting

**Observation.** Typing can feel like words going into a void. Writing by hand
is missed.

**Hypothesis.** Two separate things:

1. *The act.* Thinking by hand is its own experience; typing doesn't replace it.
2. *Somewhere to live.* The void may be the absence of a recognisable page to
   return to, continue, browse, and see beside related thoughts. A receipt
   saying where something was filed likely settles storage doubt and leaves
   the void untouched.

**Research — suggestive, modest, not about journalling.**

- Paper notebook vs tablet vs phone (Tokyo, 2021): the paper group wrote
  *faster*; accuracy was higher on easier questions against the tablet group;
  overall accuracy did not differ significantly. Spatial cues are the authors'
  proposed explanation, not a demonstrated cause.
- Lecture notes meta-analysis (2024): a small achievement advantage for
  handwritten notes, larger when notes were reviewed. College lectures only.
- EEG connectivity study (Norway, 2024): measured during writing with a digital
  pen on a touchscreen; no learning or memory tested; the typing condition was
  artificial.

The experience is reason enough to explore. The research does not need to prove
its cause.

**Explore before designing — nothing built.** A few representative pages:

- What kinds of pages are they — lists, working a thought out, diagrams?
- What does reading them yield: text accuracy, layout, drawings?
- What should coming back to a page feel like?
- What effort is acceptable? It must not add a photograph–check–file chore.

**Known gaps if pursued.** The bot takes text and voice, not photos. It would
need image storage, transcription whose uncertain readings can be corrected,
layout and diagrams preserved, and the original page kept beside searchable text.

**Gate.** Exploring costs no build. Building waits behind stage 3 unless the
user decides otherwise.
