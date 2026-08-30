# Setting up your own Trellis

Trellis is a personal second brain you run yourself: a Telegram bot backed by
Postgres, projected into an Obsidian vault, with optional Garmin integration.
One person per instance — it learns *you*, with use.

Nothing personal ships in this repo. Who you are — profile, preferences,
tracking, patterns — is built at runtime, starting with onboarding. See
"Build public" in [CLAUDE.md](../CLAUDE.md) for the principle.

## What you need

- Docker (Compose v2) — on macOS, [colima](https://github.com/abiosoft/colima) works well
- A Telegram bot token — talk to [@BotFather](https://t.me/BotFather), `/newbot`, copy the token
- Your numeric Telegram user id — ask [@userinfobot](https://t.me/userinfobot)
- An Anthropic API key — [console.anthropic.com](https://console.anthropic.com)
- An Obsidian vault folder (any empty folder works; Obsidian itself is optional —
  the vault is plain Markdown)
- Optional: a [Groq](https://console.groq.com) key for voice-note transcription
- Optional: a Garmin account for health/workout integration
- Optional: a [Tavily](https://tavily.com) key for web search

## First run

```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | What it is |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_ALLOWED_USERS` | your numeric Telegram id (comma-separated if several) |
| `ANTHROPIC_API_KEY` | your API key |
| `ANTHROPIC_MODEL` | leave the default unless you know why |
| `DATABASE_URL` | leave the default for Docker |
| `OBSIDIAN_VAULT` | absolute path to your vault folder on the host |
| `TRELLIS_TIMEZONE` | your IANA timezone, e.g. `Europe/London` |
| `TRELLIS_SECRET_KEY` | any long random string — encrypts stored Garmin sessions |
| `HEALTH_WORKER_SECRET` | any long random string — auths the Garmin worker |
| `GROQ_API_KEY` | optional, voice notes |
| `TAVILY_API_KEY` | optional, web search |

Then:

```bash
docker compose up --build -d
```

Migrations apply themselves. Message your bot on Telegram — it onboards you:
who you are, how you talk, what you want held. Everything it learns lands in
the DB and your vault, not in this repo.

## Garmin (optional)

Health data (sleep, HRV, body battery) and workout push need a Garmin
connection. With the stack running:

```bash
docker compose exec trellis python -m trellis.garmin_connect_cli
```

Sessions are encrypted with `TRELLIS_SECRET_KEY` at rest. Sync runs
automatically every 6 hours; the bot can also sync on demand.

## Day-to-day operations

```bash
docker compose logs -f trellis          # watch the bot
docker compose up --build -d            # deploy after a code change
scripts/backup_db.sh                    # dump the DB into <vault>/.backups/
docker compose exec trellis python scripts/watcher_tick.py   # force a Watcher tick
docker compose exec trellis python scripts/backfill_vault.py # re-project the vault
.venv/bin/pytest tests/ -q              # run tests (uv sync first)
```

The DB volume is your life data — never `docker compose down -v` casually.
`scripts/backup_db.sh` is designed to run nightly (cron/launchd); it needs
`OBSIDIAN_VAULT` in its environment.

## Contributing / forking

Read [CLAUDE.md](../CLAUDE.md) first — it is the working law of this codebase:
architecture (big brain + houses + rooms), file naming, tool design rules, and
the "Build public" principle. In short: the repo is generic machinery; the
person lives in the data. Nothing personal — names, meds, employers, pronouns —
is ever hardcoded, in code or prompts or tests. `scripts/check_public_hygiene.sh`
enforces a personal-marker denylist as a pre-push hook (see that script's header
to set up your own denylist).
