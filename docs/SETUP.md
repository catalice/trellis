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
| `TELEGRAM_ALLOWED_USERS` | your numeric Telegram id. Empty admits nobody — start the bot and send it `/start` to learn your id. Trellis answers in private chats only, never in groups |
| `ANTHROPIC_API_KEY` | your API key |
| `ANTHROPIC_MODEL` | leave the default unless you know why |
| `OBSIDIAN_VAULT` | absolute path to your vault folder on the host |
| `TRELLIS_TIMEZONE` | your IANA timezone, e.g. `Europe/London` |
| `POSTGRES_PASSWORD` | **required** — your own database password, any characters, set **before** the first start. There is no default. The database is only reachable from this machine |
| `TRELLIS_SECRET_KEY` | any long random string — encrypts stored Garmin sessions |
| `HEALTH_WORKER_SECRET` | any long random string — auths the Garmin worker |
| `GROQ_API_KEY` | optional, voice notes |
| `TRELLIS_CHAT_TTL_HOURS` | optional: >0 sweeps Telegram messages older than N hours (the chat matches the bot's verbatim window); 0 keeps chat forever |
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
scripts/restore_db.sh <dump>            # rehearse a restore (scratch DB, nothing live)
docker compose exec trellis python scripts/watcher_tick.py   # force a Watcher tick
docker compose exec trellis python scripts/backfill_vault.py # re-project the vault
.venv/bin/pytest tests/ -q              # run tests (uv sync first)
```

Some tests run against a real Postgres (the deployed pgvector image) and are
skipped when Docker isn't reachable. With Colima, point them at its socket:

```bash
export DOCKER_HOST=unix://$HOME/.colima/default/docker.sock
export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock TESTCONTAINERS_RYUK_DISABLED=true
```

The DB volume is your life data — never `docker compose down -v` casually.

## Backup and restore

`scripts/backup_db.sh` is designed to run nightly (cron/launchd). It reads
`OBSIDIAN_VAULT` from its environment, else from `.env`. A run that fails leaves
every earlier dump untouched; the newest 14 are kept.

The dump lands **inside the vault**, so three things together are a complete
backup, and all three must reach somewhere off this machine:

| What | Why it can't be rebuilt |
|---|---|
| The vault (including `.backups/`) | the database dumps, and anything you wrote in the vault by hand |
| `.env` | `TRELLIS_SECRET_KEY` — without it, stored Garmin sessions can't be decrypted |
| Nothing else | code is in git; generated vault pages are re-projected from the database |

Rehearse a restore now and then — it touches nothing live:

```bash
scripts/restore_db.sh <vault>/.backups/trellis-YYYY-MM-DD.sql.gz
```

It restores into a scratch database, prints what it found, and drops it. To
replace the live database:

```bash
scripts/restore_db.sh <vault>/.backups/trellis-YYYY-MM-DD.sql.gz --live
```

A live restore checks the dump in a staging database first — if it doesn't
import cleanly, nothing live is touched. It then asks you to type `restore`,
stops the bot, swaps the two databases by rename, and starts the bot. The
database it replaced is kept as `trellis_before_restore_<time>` until you drop
it yourself. Afterwards, run `scripts/backfill_vault.py` to re-project the vault.

Only one backup runs at a time: a second one started meanwhile steps aside.

## Contributing / forking

Read [CLAUDE.md](../CLAUDE.md) first — it is the working law of this codebase:
architecture (big brain + houses + rooms), file naming, tool design rules, and
the "Build public" principle. In short: the repo is generic machinery; the
person lives in the data. Nothing personal — names, meds, employers, pronouns —
is ever hardcoded, in code or prompts or tests. `scripts/check_public_hygiene.sh`
enforces a personal-marker denylist as a pre-push hook (see that script's header
to set up your own denylist).
