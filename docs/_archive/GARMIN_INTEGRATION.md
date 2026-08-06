# Garmin Integration

Status: local connection and manual read-only sync are working. Telegram sync,
scheduled sync and readiness use are not live yet.

## Boundary

`trellis.garmin.GarminClient` calls the existing Allerac health-worker contract:

| Method | Endpoint | Purpose |
|---|---|---|
| `connect` | `POST /connect` | Start Garmin authentication |
| `complete_mfa` | `POST /mfa` | Complete a pending MFA login |
| `sync` | `POST /sync` | Fetch normalized daily metrics for a date range |
| `activities` | `POST /activities` | Fetch recent or date-specific activities |
| `daily_health` | `POST /daily-health` | Fetch metrics for one date |

Every request sends JSON and the `X-Worker-Secret` header. The default transport
uses `urllib` with a configurable timeout. Tests inject a fake transport, so this
package does not require network access or another dependency.

The client returns immutable typed records:

- `GarminAuthResult`
- `GarminDailyHealth`
- `GarminActivity`

Worker field names are converted to stable snake-case Trellis names. Each health
or activity record retains the original worker object in `raw` to prevent data
loss when the worker adds fields.

## Error Contract

- `GarminConfigurationError`: missing URL, secret, or invalid timeout.
- `GarminTransportError`: timeout, network failure, or invalid JSON.
- `GarminHTTPError`: non-2xx worker response, including its status and safe detail.
- `GarminResponseError`: successful HTTP response with an invalid payload shape.
- `ValueError`: invalid caller input, such as a reversed date range.

Errors do not include the worker secret, Garmin password, MFA code, or session
dump. Callers must apply the same rule when logging request context.

## Worker Ownership

Do not import Allerac through a filesystem path. Trellis should run its own copy
or deployment of the worker, preserving Allerac as a reference.

Trellis now owns a local copy of these worker pieces:

1. `app.py`: FastAPI routes and `X-Worker-Secret` validation.
2. `garmin.py`: `garminconnect` authentication, MFA session handling, session
   restoration, metric fetching, and activity normalization.
3. `requirements.txt`: worker-only Python dependencies.
4. `Dockerfile`: isolated worker image.

The worker must be reachable from the Trellis application container. Either:

- Add the worker to Trellis's Compose network and use
  `http://health-worker:8001`; or
- Expose the worker on a host-only port for a locally running Trellis process.

Required runtime configuration will be:

```text
HEALTH_WORKER_URL=http://health-worker:8001
HEALTH_WORKER_SECRET=<random secret shared only with Trellis>
TRELLIS_SECRET_KEY=<random secret used to encrypt Garmin sessions in PostgreSQL>
```

The worker is stateless except for pending MFA sessions held in memory. A worker
restart invalidates pending MFA attempts but not completed Garmin session dumps.

## Local Setup Flow

Do not put Garmin email, password, or MFA codes in `.env`.

For the local Trellis deployment, Garmin connection is created with an interactive
command inside Docker:

```bash
docker compose run --rm trellis-bot trellis-garmin-setup
```

The command prompts for Garmin email, password, and MFA code if Garmin requires
one. It sends those values to the health worker once, then stores only the
returned Garmin session dump encrypted in PostgreSQL.

Telegram should only receive later safe commands such as `sync Garmin`; it should
not receive Garmin credentials.

## Manual Sync

After connection, a manual read-only sync can be run locally:

```bash
docker compose run --rm trellis-bot trellis-garmin-sync --days 2
```

The command:

- reads the encrypted Garmin session from PostgreSQL;
- calls the local health worker;
- stores daily health rows in `garmin_daily_health`;
- stores activity rows in `garmin_activities`;
- records sync runs in `health_sync_runs`;
- updates `garmin_connections.last_sync_at` on success;
- stores a sanitized `last_error` on failure.

First live verification on 7 June 2026 synced two daily-health records and zero
activities for 6 June 2026 to 7 June 2026.

## Ownership Still To Build

The remaining Garmin work is:

- Telegram sync/status commands.
- Optional disconnect and data-deletion flow.
- Sync jobs, retry policy and user-visible errors.
- Quiet scheduled sync, followed by readiness calculation.

Garmin measurements and user-reported measurements must both be retained with
provenance. Neither should silently overwrite the other.

## Known Worker Contract Caveat

The current Allerac worker labels activity `duration` as milliseconds. This
client therefore exposes `duration_milliseconds`. That unit must be confirmed
against a real Garmin response before training-load calculations depend on it.

The worker's activity start timestamp is generated from `startTimeLocal` by the
worker host. It should eventually return the original local timestamp and time
zone explicitly; Trellis must not infer a user's time zone from the current epoch
value.
