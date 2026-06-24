# Trellis Product Specification

**Status:** Draft for review  
**Date:** 7 June 2026  
**Purpose:** Define what Trellis is before rebuilding it.

---

## 1. Product Purpose

Trellis is a conversational external brain for Cat's personal life.

It exists to:

- close open loops;
- turn high-dimensional thinking into clear, useful structure;
- manage personal tasks without showing an overwhelming list;
- preserve and develop ideas without making every idea an obligation;
- build progressive exercise plans around real life;
- connect Garmin signals to training decisions;
- make sustained learning easier;
- provide chosen structure without becoming controlling.

> Needs: chosen structure built by myself. Trellis, not cage.

Trellis is helpful, human and direct. It remembers the full picture but surfaces only what is relevant now.

### Core Operating Model

1. **Capture** — receive thoughts naturally through text or voice.
2. **Synthesize** — flatten complex thinking into a coherent map.
3. **Organize** — route tasks, ideas, questions and context visibly.
4. **Decide** — help select what matters now.
5. **Adapt** — change plans when the body or life changes.
6. **Preserve** — keep the original input and make all important outputs inspectable.

---

## 2. Scope Boundary

### In Scope

- Personal tasks and reminders
- Brain dumps and voice transcripts
- Ideas and personal projects
- Exercise and running plans
- Garmin health and activity data
- Menstrual-cycle context
- Natural tracking for mood, emotions, symptoms, energy and medication
- Personal learning
- Obsidian synchronization

### Out of Scope

- Evinova or AstraZeneca work
- Work email, calendar, tasks or documents
- A daily web dashboard
- Medical diagnosis or treatment
- Replacing Cat's personal trainer
- Rigid habit tracking, streaks or compliance scoring
- Managing every hour of the day

There must be a clean boundary between Trellis and work systems.

---

## 3. Interfaces

### Primary Interface: Telegram

One bot. One conversation. Natural language.

Examples:

- "Morning."
- "I need to call my mum this week."
- "Remind me about the Greece payment tomorrow at 11."
- "Done with the Greece payment."
- "What should I do today?"
- "Here is everything in my head..."
- "Let's plan training."
- "I missed the social run."
- "PT moved to Friday."
- "My period started today."
- "Tell me more."

No commands or special syntax should be required. Commands may exist as optional shortcuts.

### Visible Interface: Obsidian

Nothing important should exist only inside Trellis.

Obsidian is the visible, human-readable authority. PostgreSQL is the operational engine used for querying, linking, filtering and automation.

Trellis may read and write only the files and folders it explicitly owns. It must not reorganize the rest of the vault.

---

## 4. Obsidian Routing

Cat's Obsidian framework:

| Headspace | Orienting Lens | Intention | Organizing Principle | Underlying Benefit | Guiding Question |
|---|---|---|---|---|---|
| Atlas | Knowledge | To Understand | Relatedness / Space | Learn / Remember | Where would you like to go? |
| Calendar | Time | To Focus | Time | Remember / Reflect | What's on your mind? |
| Efforts | Action | To Act | Importance | Create | What can you work on? |

Trellis must respect this model.

### Trellis-Owned Locations

```text
Calendar/
├── Tasks.md
├── trellis-idea-inbox.md
└── Trellis Captures/
    └── YYYY-MM-DD.md

Efforts/
└── <Selected Project>/

Atlas/
└── <Durable Knowledge>/
```

### Routing Rules

- Original text and voice transcripts → `Calendar/Trellis Captures/YYYY-MM-DD.md`
- Personal actions and open loops → `Calendar/Tasks.md`
- Undeveloped or unselected ideas → `Calendar/trellis-idea-inbox.md`
- Ideas selected for active work → relevant `Efforts/<Project>/` note
- Durable knowledge produced through work or learning → relevant `Atlas/` note

Ideas must not be placed directly into Atlas merely because they are interesting.

### Two-Way Synchronization

Trellis can read and write its owned files.

- Telegram changes are written to PostgreSQL and Obsidian.
- Manual edits in Trellis-owned Obsidian files are reconciled into PostgreSQL.
- Every synchronized item has a stable identifier hidden in Markdown metadata or comments.
- Trellis must not overwrite an ambiguous manual edit silently.

---

## 5. Brain Dumps and Synthesis

Brain-dump synthesis is a central product capability.

### Input

A brain dump may be:

- a short task list;
- one large idea;
- a long voice transcript;
- several connected or unrelated threads;
- emotional processing mixed with actions;
- questions, decisions and contextual information.

### Required Processing

Trellis:

1. preserves the original input unchanged;
2. identifies the central picture;
3. separates distinct threads;
4. identifies actual open loops;
5. distinguishes tasks from possible actions;
6. preserves ideas without automatically activating them;
7. identifies decisions and unanswered questions;
8. identifies context that updates existing items;
9. identifies material requiring no action;
10. links every output back to its source capture.

For exceptionally long input, Trellis processes sections first and then synthesizes across them. It must never truncate silently.

### Capture Record

```markdown
## 14:32 - Voice dump

### Synthesis
The central picture and useful interpretation.

### Processed Into
- 3 tasks -> [[Tasks]]
- Coaching framework expanded -> [[Coaching/Framework]]
- 1 idea -> [[trellis-idea-inbox#Example Idea]]

### Original
> Complete untouched transcript
```

### Response

Telegram returns a short confirmation:

> Saved the original. Added 3 tasks, developed 1 existing idea, and kept 2 observations as context.

Trellis asks a question only when ambiguity could create an incorrect commitment or destructive change.

---

## 6. Personal Tasks and Reminders

### Task Model

Each task can include:

- title;
- description;
- project or category;
- status;
- priority;
- energy demand;
- due date;
- one or more reminders;
- dependencies;
- source capture;
- postponement history;
- completion date;
- Obsidian identifier.

### Required Behaviour

Trellis can:

- create tasks from natural language;
- update tasks;
- deduplicate likely duplicates;
- correctly match a task when completed;
- show the complete list on request;
- show no more than three recommended tasks by default;
- preserve completed-task history;
- recognize tasks blocked by another person or event;
- distinguish due date from reminder time;
- pause, move or remove reminders conversationally.

### Daily Selection

When asked "What should I do?", Trellis considers:

- urgency and due dates;
- explicitly chosen focus;
- importance;
- current readiness and energy;
- available time when known;
- dependencies;
- repeated postponement;
- whether a task can be delegated, clarified or dropped.

Energy affects ordering; it must not make difficult tasks disappear forever.

### Reminder Tone

Reminders are openings, not commands.

Good:

> The Greece payment is due today. Still the priority?

Responses can be:

- "done";
- "later Friday";
- "drop it";
- "not today";
- "waiting for Gian."

Trellis should remember why something moved instead of increasing pressure blindly.

---

## 7. Ideas and Project Development

### Idea Inbox

Undeveloped ideas live in:

`Calendar/trellis-idea-inbox.md`

Each idea may include:

- title;
- synthesis;
- source capture;
- why it matters;
- related ideas or efforts;
- status: `inbox`, `incubating`, `active`, `archived`;
- optional smallest next experiment.

New input should update an existing idea where appropriate rather than creating duplicates.

### Operating Principles

During optional idea review, Trellis may use:

`Atlas/Self/Operating Principles.md`

Questions include:

- Who is this for?
- What problem might it solve?
- What is evidence, inference or speculation?
- What is the smallest external artifact?
- Has this idea earned active attention?

These questions must not interrupt initial capture.

### Promotion

- Inbox or incubating idea → remains in Calendar
- Selected active idea → becomes or joins an Effort
- Durable learning from the effort → becomes or links to Atlas

---

## 8. Training Purpose

Trellis builds a progressive exercise programme toward a defined goal while respecting that training supports life rather than replacing it.

> Optimize for the long-term upward trend, not perfect daily compliance.

Life events are inputs to the plan, not failures against it.

### Current Goal

- Complete a half marathon.
- Primary performance goal: finish strongly and sustainably.
- Stretch goal: `2:00`.

The stretch goal informs progression and pace development, but must not turn every week into a test. Trellis should periodically reassess whether the goal and timeline remain realistic from completed training.

### Training Hierarchy

```text
Goal
  -> Training phase
  -> Weekly training plan
  -> Today's adjusted session
  -> Completed activity
  -> Review and next progression
```

The weekly training plan is specifically an exercise plan. It is not a full personal schedule.

---

## 9. Current Training Structure

### Existing Anchors

- Two personal strength-training sessions each week
- Wednesday social run

Cat's personal trainer owns the strength programme. Trellis records and plans around those sessions but does not redesign them.

The social run is a preferred session, not an immovable appointment. Cat may say in advance that she cannot attend, and Trellis should redesign that week accordingly.

### Running Structure

Current expected running structure:

- Wednesday social run normally provides the weekly hard-running stimulus
- One easy Zone 2 run
- One progressively longer easy run
- Optional fourth easy run only when training phase, recovery and life load support it

Trellis should avoid:

- placing hard running beside demanding lower-body strength work;
- adding another hard run while the social run is sufficiently demanding;
- increasing volume and intensity simultaneously.

### Hard-Session Progression

For now:

> The social run counts as the weekly hard session while it produces a genuinely hard training stimulus.

This classification is based on Cat's actual effort and training response, not merely attendance, pace, or achieving a particular milestone.

Track:

- pace;
- average and maximum heart rate;
- distance;
- perceived effort;
- recovery afterward;
- heat, humidity, terrain and cycle context.

When the social run no longer provides enough stimulus, Trellis replaces or supplements it with a more purposeful hard session such as tempo, intervals, hills or progression running. It must still avoid adding intensity merely because one run felt easy.

The decision uses a sustained trend across:

- perceived effort and ability to speak;
- heart-rate response relative to Cat's own history;
- pace and terrain;
- recovery during the following 24-48 hours;
- the current training phase and half-marathon goal.

Approximately `6:00-6:30/km` at around `145 bpm` may be a useful personal reference point, but it is not the rule that triggers progression.

### Mobility and Physical Maintenance

Mobility and general physical maintenance are part of the training system, not an optional afterthought.

Trellis should:

- account for recurring niggles, contractures, lower-back discomfort and general physical tension;
- schedule short mobility or recovery sessions where they are most likely to help;
- preserve exercises prescribed by Cat's trainer or another qualified professional;
- distinguish routine stiffness from pain that should change training;
- notice recurring patterns across running, strength work, travel, sleep and cycle context;
- avoid pretending to diagnose or treat an injury.

Mobility should be lightweight and specific. Trellis should not fill every rest day with another obligation.

### Run Activation and Recovery

Every prescribed run includes the preparation and recovery needed for that specific session.

Every session also shows its complete time commitment, including activation, running, recoveries and post-run work. Trellis must not describe a `45-minute run` when the actual commitment is over an hour.

Before running, Trellis provides a short activation sequence that:

- raises temperature progressively;
- prepares ankles, calves, hips and trunk;
- rehearses useful running mechanics;
- develops appropriate qualities through drills such as pogo jumps, A-skips or controlled strides;
- changes according to whether the run is easy, long or hard.

After running, Trellis provides an exact cool-down or mobility sequence appropriate to the session and Cat's current physical context.

Instructions must specify:

- exercise name;
- repetitions, duration or distance;
- number of sets;
- rest where relevant;
- execution order;
- one short technique cue when needed.

Example:

```text
Expected duration — 59 minutes
Reserve — 1 hour 5 minutes

Activation — 8 minutes
1. Brisk walk: 2 minutes
2. Ankle rocks: 10 each side
3. Pogo jumps: 2 x 20, resting 20 seconds
4. A-skips: 2 x 20 metres
5. Progressive strides: 3 x 20 seconds, walking 40 seconds between

Run — 42 minutes
10 minutes easy
4 x 4 minutes controlled hard, with 2 minutes easy jog between
10 minutes easy

After — 9 minutes
1. Walk: 3 minutes
2. Calf stretch: 2 x 30 seconds each side
3. Hip-flexor stretch: 2 x 30 seconds each side
```

The displayed total may be rounded upward to a practical calendar block, such as `1 hour 15 minutes`, so Cat can reserve enough time without calculating it.

Evening mobility or Yin yoga may be scheduled when it serves recovery, down-regulation or a recurring physical need. It should not be added automatically every day or used to create a second compulsory training programme.

### Personal Training-Response Learning

Trellis learns from what Cat actually completes and how she responds, not only from the plan it generated.

It may identify patterns such as:

- running performance and perceived effort by time of day;
- whether three or four weekly runs produce better progress and recovery;
- response to different long-run frequencies and progression rates;
- interaction between strength sessions and subsequent runs;
- recovery after hard sessions;
- effects associated with sleep, life load, travel, heat and cycle context;
- which mobility work is associated with fewer recurring symptoms;
- the difference between planned duration and the time sessions actually require.

For each completed session, Trellis should retain:

- session type and intended stimulus;
- start time and day;
- planned and actual duration;
- activation, run and recovery completed;
- distance, pace, heart rate and available Garmin metrics;
- Garmin workout segments, laps and intervals, without assuming laps are
  kilometre splits;
- perceived effort and recovery;
- relevant pain, symptoms and context;
- surrounding weekly structure.

Trellis must distinguish:

- **observation** — what happened;
- **possible pattern** — a relationship worth watching;
- **supported personal pattern** — repeated enough to influence planning;
- **experiment** — a deliberate temporary change used to compare outcomes.

Example:

> Your last six comparable easy runs before 10:00 had lower heart rate at the same pace and better reported energy than afternoon runs. I will place easy runs in the morning when the week allows and keep checking the pattern.

It must account for confounders such as route, weather, distance, training phase, sleep and recent strength work. It should report uncertainty and should not infer that three runs are better than four until enough comparable weeks exist.

Learned patterns are recommendations, not permanent rules. Cat can reject, correct or deliberately test them, and Trellis updates its model.

---

## 10. Weekly Training Planning

The weekly training plan builds toward the stored goal and current training phase.

Inputs:

- target event and goal;
- current baseline;
- previous weeks' planned and completed training;
- weekly running volume;
- longest recent run;
- social-run trend;
- strength sessions;
- mobility needs and recurring niggles;
- pain, illness and fatigue;
- Garmin health and activity trends;
- menstrual-cycle context;
- chronotype and preferred training times;
- travel, holidays and social commitments;
- subjective body state and life load.

The result includes:

- seven-day structure;
- the complete reserved time for each workout block;
- exact run purpose and intensity;
- precise activation and post-run instructions for each run;
- any useful mobility or physical-maintenance work;
- rest and recovery;
- relationship to strength sessions;
- brief rationale;
- what changed from the previous week.

Each week Trellis chooses to:

- progress slightly;
- maintain;
- deload;
- rebuild after disruption.

---

## 11. Missed and Changed Training

The weekly plan remains editable throughout the week. Trellis recalculates from the current moment whenever availability, work, recovery, symptoms or preference changes. It never makes up training mechanically.

Examples:

- "I can't do the social run this evening. Give me a hard run for this morning."
- "Work ran late. Move today's session."
- "My legs are heavy. What changes?"
- "I have an unexpected free hour tomorrow."

For a same-day change, Trellis considers:

- whether the requested time is still in the future;
- the time available;
- training completed in the previous 48 hours;
- strength sessions before and after it;
- current recovery, pain and symptoms;
- the effect on the rest of the week;
- practical conditions such as heat, route and daylight when known.

It then provides the revised session directly and updates the remainder of the week. It asks a question only when missing information materially affects safety or makes the request impossible to interpret.

### Strength Session Missed or Moved

- Ask whether it was cancelled or moved if unclear.
- If moved, reorganize nearby runs.
- Account for the expected strength load.

### Social Run Unavailable or Missed

- If known in advance, redesign the week and schedule an appropriate hard session elsewhere when the phase, recovery and strength sessions support it.
- If missed unexpectedly, recalculate from the remaining days rather than treating the original plan as fixed.
- Replace it with a controlled hard session only when useful and safely placed.
- Skip intensity when recovery or life load makes that more appropriate.
- Do not treat missing the social element as a training failure.

### Easy Run Missed

- Usually let it go.
- Do not redistribute every missed kilometre.

### Long Run Missed

- Move it only when a sensible recovery window exists.
- Otherwise accept a lower-volume week and adjust future progression.

### Several Sessions Missed

- Treat as disruption or an unplanned deload.
- Reassess the next week's load.
- Do not punish or create backlog.

---

## 12. Holidays, Travel and Life

Holidays should be used intelligently:

- planned recovery;
- maintenance;
- natural deload;
- walking, swimming or enjoyable movement;
- short easy runs when they genuinely fit.

Nothing is "owed" afterward.

Example:

> This is a useful deload week. Two short easy runs if they fit, plenty of walking, and no sessions to make up when you return.

Training decisions prioritize:

1. Safety
2. Life reality
3. Meaningful progress toward the goal
4. Preference among valid options

Trellis should sometimes challenge avoidance, but must distinguish it from poor recovery, pain, illness or genuine overload.

---

## 13. Garmin, Readiness and Self-Report

### Automatic Background Sync

Trellis quietly fetches and stores:

- sleep duration and stages;
- HRV;
- resting and average heart rate;
- body battery;
- stress;
- recent activities;
- training history.

Background synchronization does not require a notification.

### Morning Interaction

When Cat says "morning", Trellis:

1. syncs or reads current Garmin data;
2. calculates readiness;
3. considers cycle and recent trends;
4. presents a short meaningful summary;
5. adjusts today's existing training session if required;
6. optionally provides the next learning lesson.

### Data Conflicts

Preserve both measured and reported experience.

Example:

```text
Garmin: 6h 04m sleep
Self-report: approximately 7h and felt well-rested
```

- Garmin provides measured data.
- Self-report provides perception, missing context and corrections.
- Neither silently overwrites the other.
- An explicit user correction is stored as an override while retaining the original measurement.

Readiness should use trends and context, not treat one number as truth.

---

## 14. Menstrual-Cycle Context

Trellis records:

- reported period start;
- reported period end when provided;
- cycle length and variability over time;
- estimated cycle day and phase;
- symptoms;
- bleeding, pain, mood or other observations Cat chooses to record;
- observed effects on sleep, energy, heart rate and training.

Cycle phase informs interpretation but does not dictate behaviour mechanically.

Trellis should learn Cat's actual patterns over time rather than assuming generic effects always apply.

Cycle estimates must be labelled as estimates. Trellis is not a contraceptive, fertility or medical decision tool.

### Mood, Symptoms and Medication Context

Later Trellis should let Cat record mood, emotions, symptoms, energy and
medication conversationally, without turning it into rigid habit tracking.

Examples:

- "Mood dipped this afternoon, probably tired and overwhelmed."
- "Lower back tight again, 4/10, not sharp."
- "Took medication at 8:30."
- "Energy crashed after lunch."

This context should be stored with provenance and date/time. It can inform
readiness, training adaptation and pattern review, but it must not diagnose,
over-interpret or punish normal variability.

---

## 15. Learning

Trellis supports structured, persistent learning.

It remembers:

- curriculum;
- current position;
- lessons delivered;
- topics explored in depth;
- follow-up questions;
- useful knowledge exported to Atlas.

Delivery options:

- with the first morning interaction;
- on request;
- scheduled delivery only when explicitly enabled.

Examples:

- "What's next in learning?"
- "Tell me more."
- "Save this to Atlas."

Learning should continue coherently rather than restarting at lesson zero.

---

## 16. Human Interaction Contract

Trellis should feel like a capable, warm human assistant rather than another reminder system.

### Design Principles

- Direct, warm and concise
- Concrete, complete and easy to execute
- Helpful rather than performatively encouraging
- Honest when something matters
- No guilt or moral judgement
- No infantilizing language
- No notification spam
- No unnecessary decisions
- No turning every idea into work
- No treating rest or enjoyment as failure

### Instruction Style

Anything Trellis asks Cat to do must minimize procrastination and decision fatigue.

- Give one clear recommendation, not a menu of equivalent options.
- Use exact repetitions, durations, distances, sets, rests and intensity.
- Put actions in execution order.
- Use plain names and short technique cues.
- State the full session rather than requiring Cat to assemble it.
- Do not use vague instructions such as "do some mobility", "warm up properly" or "take it easy".
- Do not soften instructions with "if you like", "whatever you prefer" or unnecessary preference questions.
- Do not use dismissive simplifications such as "just do these three things and you're done".
- Ask for a choice only when Cat's preference changes the goal, commitment or a materially different valid plan.

Direct does not mean harsh. Trellis gives a clear plan without pressure, guilt or rough language.

### Enforceable Runtime Rules

- Maximum three tasks surfaced by default
- No unsolicited check-ins unless enabled
- Configurable quiet hours
- No personal-task reminders during quiet hours
- No missed-task or missed-training backlog
- No uncertain task creation without confirmation
- No work-system data
- Every reminder can be moved, paused or removed conversationally
- Automations can be paused conversationally
- Missed training adapts forward
- Important data remains visible in Obsidian

Preferences are stored and adjustable through Telegram.

---

## 17. Technical Architecture

Trellis is a separate project and repository. It does not require the Allerac frontend.

```text
Telegram
    |
    v
Trellis application
    |- Capture and synthesis
    |- Tasks and reminders
    |- Ideas and project routing
    |- Training planning
    |- Readiness and adaptation
    |- Learning
    |- Obsidian synchronization
    |
    +--> PostgreSQL + pgvector
    +--> Garmin health worker
    +--> Obsidian vault
```

### Reuse From Allerac

- PostgreSQL and pgvector configuration
- Garmin health-worker logic
- Relevant health database migrations
- Credential-encryption approach
- Tested Garmin normalization logic

### Do Not Reuse

- Next.js frontend
- Allerac chat interface
- Ollama requirement
- Executor or shell agent
- Allerac's full monitoring stack: Prometheus, Grafana, Loki, Promtail and node-exporter
- Allerac domains, skills or RAG framework
- Legacy Trellis bot architectures

Trellis still needs ordinary operational visibility: structured application logs, service health checks and clear error reporting. It does not initially need a separate metrics dashboard and centralized log platform for one local user.

### Runtime Services

Initial:

- `postgres`
- `health-worker`
- `trellis-bot`

A separate background worker should be added only when background jobs genuinely require it.

---

## 18. Product Modules and Deployment

Trellis should be one coherent product for Cat, built from modules with explicit boundaries:

- **Trellis Capture** — brain dumps, synthesis, ideas and Obsidian routing
- **Trellis Tasks** — tasks, selection and reminders
- **Trellis Training** — goals, plans, Garmin, cycle context, mobility and adaptation
- **Trellis Learning** — curricula, lessons, progress and Atlas export
- **Trellis Core** — conversation, identity, preferences, scheduling and synchronization

For Cat, these modules share one Telegram conversation and can reason across the whole picture. For example, Training may account for life load without exposing private task details unnecessarily.

The modules should also be separable later into focused products or deployment profiles. Someone could run Training only, Tasks only or Learning only without installing the whole system.

This requires:

- separate domain services and database ownership;
- explicit interfaces between modules;
- feature flags or deployment configuration;
- no Training dependency on the Tasks user interface;
- no module assuming Cat-specific preferences are universal.

The first build should remain a modular monolith. Separate products do not justify separate repositories or distributed services yet.

---

## 19. Data Ownership

### PostgreSQL

Operational state:

- captures and links;
- tasks and reminders;
- ideas;
- training goals, phases, plans and sessions;
- readiness;
- health metrics and activities;
- cycle state;
- learning progress;
- synchronization metadata;
- user preferences.

### Obsidian

Visible and editable records:

- original captures;
- task list and completed history;
- idea inbox;
- developed effort notes;
- readable training plans and progress;
- selected learning notes.

### Garmin

Source of measured health and activity data.

### Conflict Rule

- Preserve provenance.
- Do not silently discard either source.
- Manual explicit corrections override behaviour, not historical source records.
- Ambiguous Obsidian edits require reconciliation rather than blind overwrite.

---

## 20. Release Scope

### MVP

The first release is complete only when these workflows work end to end:

1. A text or long voice dump is preserved, synthesized and routed visibly.
2. Tasks can be created, listed, updated, reminded and correctly completed.
3. Ideas are merged into `Calendar/trellis-idea-inbox.md`.
4. "What should I do?" returns three sensible personal tasks.
5. A half-marathon goal and phase produce a progressive weekly plan covering running, strength anchors and appropriate mobility.
6. A missed, moved or pre-declined social run adapts the current week correctly.
7. "Morning" uses Garmin data, period context and subjective context to adjust today's planned session.
8. Period starts, symptoms and personal cycle observations can be recorded conversationally and inspected.
9. Every prescribed workout shows its complete practical time block.
10. Completed training retains the context needed for later personal-pattern analysis.
11. PostgreSQL and Trellis-owned Obsidian files remain synchronized.

### After MVP

- Automated personal-pattern conclusions and training experiments
- Task-management pattern learning:
  - completion patterns by time of day, energy level and task type;
  - repeated postponement and the reasons tasks move;
  - realistic personal capacity rather than assumed capacity;
  - reminder timing and wording associated with useful action;
  - tasks that benefit from urgency, clarification, decomposition or another person's presence;
  - captured tasks later dropped or judged unimportant.
- Semantic idea and capture retrieval
- More advanced voice processing
- Persistent learning curriculum, next lesson, follow-up and progress
- Optional scheduled learning
- Advanced learning exploration and automatic Atlas synthesis
- Richer cycle-pattern analysis
- Optional read-only review interface

Learning remains a core Trellis module, but follows the first MVP so it does not delay a trustworthy capture, task and training foundation. Its domain boundary and storage needs should be preserved during MVP design.

---

## 21. Acceptance Scenarios

### Brain Dump

**Input:** A ten-minute voice transcript containing wedding tasks, a Scotland question, emotional processing and a product idea.

**Expected:**

- original transcript saved;
- coherent synthesis produced;
- real tasks merged into Tasks;
- idea merged into the idea inbox;
- question and non-action reflection retained;
- all outputs linked to the capture;
- short Telegram confirmation.

### Reminder

**Input:** "Remind me to pay Greece tomorrow at 11."

**Expected:**

- task created or matched;
- reminder stored;
- Obsidian updated;
- notification sent at the requested time;
- "later Friday" reschedules it without duplication.

### Task Completion

**Input:** "Done calling mum."

**Expected:**

- the matching task is completed;
- no unrelated task changes;
- PostgreSQL and Obsidian update;
- completed history retained.

### Weekly Training

**Input:** "Body 7, life load 8, PT Monday and Thursday, dinner Friday, away this weekend."

**Expected:**

- plan accounts for both PT sessions;
- Wednesday social run remains the hard session when appropriate;
- volume reflects high life load and weekend travel;
- no punitive make-up sessions;
- progression state remains intact.

### Social Run Declined in Advance

**Input:** "I can't make the social run on Wednesday this week."

**Expected:**

- Trellis removes it from the active plan;
- evaluates strength sessions, recovery and the rest of the week;
- schedules a suitable hard-running session elsewhere when useful;
- otherwise explains why this week should contain no hard session;
- updates the visible plan without guilt or backlog.

### Same-Day Training Change

**Input:** "I can't do the social run this evening. Give me a hard run for this morning."

**Expected:**

- Trellis checks whether a hard morning session fits the current week and recovery state;
- when suitable, it supplies one complete session with exact activation, running intervals, recoveries and post-run work;
- it updates the evening and remaining weekly plan;
- when unsuitable, it gives one clear safer session and briefly explains the constraint;
- it does not present a list of alternatives or ask unnecessary preference questions.

### Missed Social Run

**Input:** "I missed the social run."

**Expected:**

- Trellis evaluates the remainder of the week;
- does not automatically insert intervals tomorrow;
- explains whether intensity is replaced or skipped;
- updates the visible plan.

### Holiday

**Input:** "I'm away in Greece next week and want to enjoy it."

**Expected:**

- plan uses the holiday as maintenance or deload;
- optional enjoyable movement only;
- no guilt;
- nothing is owed afterward.

### Morning

**Input:** "Morning. Slept badly but feel surprisingly okay."

**Expected:**

- measured Garmin data and subjective report both retained;
- trends considered;
- today's existing session adjusted only if necessary;
- response is concise and human.

### Period and Mobility

**Input:** "My period started today. My lower back is tight again, but it isn't sharp pain."

**Expected:**

- period start and self-reported symptom are retained;
- cycle estimate is updated and labelled as an estimate;
- today's training and mobility are adjusted only when warranted;
- recurring lower-back reports can be reviewed as a pattern;
- Trellis does not diagnose the cause.

### Learning

**Input:** "What's next in Big Bang to Modern?"

**Expected:**

- the next lesson follows the stored curriculum position;
- it does not restart at lesson zero;
- follow-up exploration remains linked to the lesson;
- progress persists for the next interaction.

---

## 22. Definition of Success

Trellis succeeds when:

- Cat can empty her head without creating more organizational work;
- thoughts become clear, visible and retrievable;
- tasks close rather than disappear into a system;
- ideas remain available without all becoming active commitments;
- training progresses toward a sustainable half marathon and the `2:00` stretch goal while adapting to real life;
- strength, running, mobility and recurring physical issues are considered together;
- holidays, missed days and poor recovery are handled constructively;
- the system is inspectable and trustworthy through Obsidian;
- using Trellis feels lighter than remembering everything alone.
