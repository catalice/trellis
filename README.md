# Trellis

Trellis is a conversational external brain for personal tasks, captures and adaptive training.

This repository is the clean rebuild. The legacy Trellis and Allerac repositories remain references; this project does not depend on their frontends or runtime.

The canonical product definition is [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md).

The plain-language record of what is and is not built is
[docs/BUILD_STATUS.md](docs/BUILD_STATUS.md).

The parallel module and sequential deployment plan is
[docs/INTEGRATION_PLAN.md](docs/INTEGRATION_PLAN.md).

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
