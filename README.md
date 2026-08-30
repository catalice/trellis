# Trellis

> *Good design treats systems like gardens, not machines. Some things anchor, some bloom seasonally, some need space to run wild.*

Trellis is a personal operating system — an externalised mind. A big brain (the always-on core) with houses that light up by meaning: Move (training), Sense (wellbeing), Focus (organising), Learn (someday). Semantic routing, one Claude call per turn, everything inspectable in an Obsidian vault. Not a bot with features — a trellis: chosen structure that something living grows on.

**Build public.** Nothing personal is hardcoded here — no names, no meds, no
employers, no assumptions about who you are. The repo is generic machinery; the
person lives in the Trellis brain (profile, preferences, memory, tracking,
vault), built by its user, with use. If a feature needs personal knowledge, it
learns it at runtime — it never ships it.

Run your own: [docs/SETUP.md](docs/SETUP.md).

The product direction lives in [docs/DIRECTION.md](docs/DIRECTION.md).

The plain-language record of what is and is not built is
[docs/BUILD_STATUS.md](docs/BUILD_STATUS.md).

The working rules for contributing (architecture, naming law, tool design,
the Build-public principle) are in [CLAUDE.md](CLAUDE.md).

## First Vertical Slice

The initial implementation supports:

- preserving and synthesizing text brain dumps with Claude;
- routing concrete actions to tasks and exploratory material to the idea inbox;
- correcting task-to-idea misclassifications conversationally;
- writing dated source captures into Obsidian;
- creating tasks from Telegram;
- listing open tasks;
- selecting up to three tasks for today;
- completing the correct task by name;
- retaining task lifecycle events for later pattern learning;
- projecting the current task state into a Trellis-owned block in Obsidian.

The current build does not yet implement voice transcription, working reminders, Garmin, training plans or curriculum learning.

## Run Locally

1. Copy `.env.example` to `.env` and set the Telegram token.
2. Start PostgreSQL:

   ```bash
   docker compose up -d postgres
   ```

3. Install and run:

   ```bash
   uv sync
   uv run trellis
   ```

The database migration runs automatically when the bot starts.

## Test

```bash
uv run python -m unittest discover -s tests -v
```

Tests use an in-memory repository and a temporary Obsidian vault. They do not modify the real vault.
