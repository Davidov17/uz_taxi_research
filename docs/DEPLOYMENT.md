# Deployment

How to run the full production system — Telegram bot, admin dashboard,
Postgres, and persistent screenshot storage — on a clean machine, and how
to operate it afterward (migrations, backups, updates, logs).

## Architecture

The simplest reliable shape for this project is **one Docker Compose stack
on a single host**: four containers built from one shared image, one
Postgres instance, two named volumes. There's no separate load balancer,
message queue, or object-storage service — the bot talks to Telegram over
long polling (no public HTTPS endpoint required), and the dashboard is a
single FastAPI process serving both the JSON API and the static frontend.
This is deliberately not a multi-node/Kubernetes setup: the survey volume
this system is designed for (interviewer-driven, not high-throughput) does
not need it, and a single-host Compose stack is dramatically easier to
operate, back up, and reason about.

```
                       ┌────────────────────┐
                       │      postgres       │  postgres:16-alpine
                       │  (pgdata volume)     │
                       └─────────┬────────────┘
                                 │
                   ┌─────────────┼──────────────┐
                   │                             │
          ┌────────┴────────┐          ┌─────────┴────────┐
          │     migrate      │          │        …         │
          │  alembic upgrade │          │   (runs once,    │
          │      head        │          │    then exits)   │
          └────────┬─────────┘          └───────────────────┘
                   │ (must succeed first)
        ┌──────────┴──────────┐
        │                     │
┌───────┴────────┐   ┌────────┴────────┐
│      bot         │   │       web        │
│ long-polling      │   │  FastAPI + Chart.js│
│ python -m          │   │  dashboard, :8000  │
│ telegram_bot.main  │   │                    │
└───────┬────────┘   └────────┬────────┘
        │                     │
        └──────────┬──────────┘
                    │
           ┌────────┴─────────┐
           │   screenshots      │  named volume, mounted
           │  (named volume)    │  read-write by both
           └────────────────────┘
```

All four containers (`postgres` excluded) are built from the **same
image** (the root `Dockerfile`) — `bot`, `web`, and `migrate` differ only
in the command they run. This means one build, one set of dependencies to
keep in sync, and no risk of the bot and dashboard drifting onto different
versions of the application code.

## Prerequisites

- A Linux host (or any machine) with **Docker Engine 24+** and the
  **Docker Compose plugin** (`docker compose version` should work — if you
  only have the older standalone `docker-compose`, replace `docker
  compose` with `docker-compose` throughout this doc).
- A Telegram bot token from [@BotFather](https://t.me/BotFather) (send
  `/newbot`, follow the prompts, copy the token it gives you).
- Outbound internet access from the host (the bot polls
  `api.telegram.org`; nothing needs to be reachable *from* the internet
  unless you choose to expose the dashboard beyond the host itself).

## Quick start (clean machine)

```bash
# 1. Get the code onto the machine
git clone <this-repository-url> market-research-bot
cd market-research-bot

# 2. Configure
cp .env.example .env
nano .env    # set BOT_TOKEN and a real POSTGRES_PASSWORD at minimum

# 3. Build the image
docker compose build

# 4. Start Postgres and apply migrations
docker compose up -d postgres
docker compose run --rm migrate     # applies every migration, then exits

# 5. Start the bot and the dashboard
docker compose up -d bot web

# 6. Check everything is healthy
docker compose ps
curl http://localhost:8000/health   # -> {"status":"ok"}
```

Open `http://<host>:8000/` in a browser for the admin dashboard, and
message your bot on Telegram to confirm `/start` responds.

In normal day-to-day use, `docker compose up -d` alone is enough — Compose
starts services in dependency order (`postgres` → `migrate` → `bot`/`web`,
per the `depends_on: condition: service_completed_successfully` wiring in
`docker-compose.yml`) so migrations always run before the bot or dashboard
can serve traffic.

## Configuration

Every setting is an environment variable, read from `.env` by Docker
Compose (for container-level substitution) and by the application itself
via `pydantic-settings` (`src/telegram_bot/infrastructure/config.py`).
**Nothing is hardcoded and nothing is committed** — `.env` is in
`.gitignore`; `.env.example` is the checked-in template with no real
secrets.

| Variable | Used by | Purpose |
|---|---|---|
| `BOT_TOKEN` | bot | Telegram bot token from @BotFather. The bot refuses to start without it (clear `RuntimeError`, not a silent hang). |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | postgres, docker-compose.yml | Credentials for the `postgres` container; docker-compose.yml builds `DATABASE_URL` for bot/web/migrate from these automatically. |
| `DATABASE_URL` | local (non-Docker) runs, tests | Full SQLAlchemy async URL. Docker Compose ignores this and constructs its own from the `POSTGRES_*` vars instead — it only matters when running `uv run ...` directly on the host. |
| `SCREENSHOT_STORAGE_DIR` | bot, web | Where uploaded screenshots are cached to disk. Fixed to `/data/screenshots` inside Docker Compose (the `screenshots` volume); only the local-run default (`./data/screenshots`) is configurable via this var. |
| `LOG_LEVEL` | bot, web | `debug` / `info` / `warning` / `error`. |
| `ENVIRONMENT` | bot, web | Informational only — logged at startup, doesn't change behavior. |
| `WEB_PORT` | docker-compose.yml | Host port the dashboard is published on (container always listens on 8000 internally). |

Change any of these by editing `.env` and running `docker compose up -d`
again — Compose recreates only the containers whose config actually
changed.

## Database migrations

Migrations are plain Alembic, unchanged from development — `migrations/`
holds the same chain used throughout this project. In production they run
as a **one-shot `migrate` service** rather than baked into the bot/web
image's startup command, specifically so two containers never race to
apply the same migration concurrently, and so a migration failure is
clearly visible as its own failed container rather than buried in bot or
web logs.

```bash
# Apply every pending migration (safe to run repeatedly — it's a no-op at head)
docker compose run --rm migrate

# Check what's currently applied, without changing anything
docker compose run --rm migrate alembic current

# Roll back one migration (rare — see the caution below)
docker compose run --rm migrate alembic downgrade -1
```

**Before running a new migration against a database with real survey
data**, take a backup first (see below) — Alembic's downgrades for this
project include real `DROP COLUMN`/`DROP TYPE` statements where
appropriate (not auto-generated no-ops), so a downgrade is genuinely
destructive to any data stored in the columns it removes.

To apply migrations without Docker at all (e.g. you're running Postgres
and the app directly on the host instead of in containers):

```bash
uv run alembic upgrade head
```

## Health check

`GET /health` on the web service does a real round trip to Postgres
(`SELECT 1`), not just "the process is up" — a database outage shows up
as an unhealthy container, not a dashboard that silently serves errors.
It's wired into `docker-compose.yml`'s `healthcheck:` for the `web`
service (`docker compose ps` shows `healthy`/`unhealthy`), and is the
right thing to point any external uptime monitor or reverse-proxy health
probe at.

The `bot` service has no HTTP endpoint of its own (Telegram long-polling
has no natural place to hang one) — its liveness is `restart:
unless-stopped` plus `docker compose logs bot`. This is the traded-off
simplicity mentioned in the architecture section: a webhook-based bot
could expose `/health` too, but would also require a public HTTPS
endpoint and TLS termination, which is a materially bigger production
surface for no benefit at this project's scale.

## Logging

Both the bot and the web process use one shared setup
(`infrastructure/logging_config.py`): structured single-line log records
(`timestamp LEVEL logger: message`) to stdout/stderr, level controlled by
`LOG_LEVEL`. Nothing is written to a log file inside the container —
that's deliberate, so `docker compose logs` / your platform's log
collector (journald, a logging driver, etc.) is the one place logs live,
rather than a second copy going stale inside a container filesystem.

```bash
docker compose logs -f bot        # follow the bot's logs
docker compose logs -f web        # follow the dashboard's logs
docker compose logs --since 1h    # everything from the last hour
```

Noisy driver-level logging (SQLAlchemy query logging, aiogram's
per-update event log) is suppressed to `WARNING` unless `LOG_LEVEL=DEBUG`
is set, so normal `INFO` logs stay readable.

## Graceful shutdown

Both processes handle `SIGTERM` (what `docker compose stop` / `docker
compose down` sends) cleanly rather than being killed mid-request:

- **bot**: aiogram's `Dispatcher.start_polling()` installs `SIGTERM`/
  `SIGINT` handlers by default, which stop the polling loop after the
  in-flight update finishes rather than aborting it. `main.py`'s `finally`
  block then disposes the database connection pool before the process
  exits — verified directly (this doc's author ran the bot with a live
  connection, sent it `SIGTERM`, and confirmed via `pg_stat_activity` that
  no connections were left open afterward).
- **web**: `uvicorn` itself handles `SIGTERM` by finishing in-flight HTTP
  requests before shutting down; the app's `lifespan` handler additionally
  disposes the database engine on shutdown.
- The `Dockerfile`'s `CMD` uses exec form (`["python", "-m", ...]`, not a
  shell string), so `SIGTERM` reaches the Python process directly instead
  of being swallowed by an intermediate shell.

Give the stack a few seconds to stop cleanly rather than force-killing it:

```bash
docker compose stop        # sends SIGTERM, waits (default 10s) before SIGKILL
```

## Backups

**Database.** The only thing that's genuinely hard to reconstruct if
lost. Recommended: a nightly `pg_dump` to a file outside the `pgdata`
volume (ideally off-host — sync it to object storage or another machine).

```bash
# One-off backup
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
  | gzip > "backup_$(date +%Y%m%d_%H%M%S).sql.gz"

# Restore (into a fresh/empty database — this does not merge)
gunzip -c backup_20260101_020000.sql.gz | docker compose exec -T postgres \
  psql -U "$POSTGRES_USER" "$POSTGRES_DB"
```

A simple cron entry for a nightly 2am backup, keeping 14 days:

```cron
0 2 * * * cd /path/to/market-research-bot && \
  docker compose exec -T postgres pg_dump -U market_research_bot market_research_bot | gzip > /var/backups/mrb/backup_$(date +\%Y\%m\%d).sql.gz && \
  find /var/backups/mrb -name 'backup_*.sql.gz' -mtime +14 -delete
```

**Screenshots.** The `screenshots` named volume holds every downloaded
screenshot (`docker volume inspect market-research-bot_screenshots` shows
its host path). Back it up alongside the database — a plain `tar`/`rsync`
of that path, or a `docker run --rm -v market-research-bot_screenshots:/data -v $(pwd):/backup alpine tar czf /backup/screenshots.tar.gz -C /data .`
snapshot, run on the same schedule as the database dump. Losing this
volume loses cached images but not data: every attachment's Telegram
`file_id`/`file_unique_id` is still in the database, so metadata survives
even if the cached image doesn't (Telegram's own copy typically expires
after a while, though, so don't treat the volume as disposable).

**What NOT to rely on**: Docker volumes are not backups by themselves —
they protect against a container restart, not against host disk failure,
`docker volume rm`, or a bad migration. Actually copy the dump/tarball
somewhere else.

## Updating / redeploying

```bash
git pull
docker compose build
docker compose run --rm migrate      # apply any new migrations first
docker compose up -d bot web         # recreates only what changed
```

Compose recreates a container only if its image or config changed, so
this is safe to run even when nothing changed (it's a no-op).

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `bot` container restart-loops, logs `TelegramUnauthorizedError` | `BOT_TOKEN` is wrong or revoked — regenerate it with @BotFather and update `.env`. |
| `bot`/`web` exit immediately with `BOT_TOKEN is not set` / connection refused | `.env` wasn't filled in, or wasn't picked up — confirm `docker compose config` shows the value you expect. |
| `migrate` fails | Check `docker compose logs migrate`; almost always a Postgres connectivity issue (wait for `postgres` to report healthy) or a genuinely broken migration (check `alembic current` vs `alembic heads`). |
| Dashboard shows `{"status":"ok"}` at `/health` but survey pages 404 | That's often correct — `/health` only checks DB connectivity, not that any surveys exist yet. |
| Screenshot upload works but the dashboard shows "not cached" | The bot's download to `/data/screenshots` failed (logged as a `WARNING`, not an error — check `docker compose logs bot`); the Telegram `file_id` is still recorded either way. |

## Security notes

- The container runs as a non-root user (`app`), not root.
- No secrets are baked into the image — everything sensitive comes from
  `.env` at container start.
- The dashboard has **no authentication built in** — it's designed to run
  on a host/network the research team already trusts (e.g. behind a VPN,
  or only bound to `localhost` and reached via SSH tunnel). If you expose
  `WEB_PORT` beyond a trusted network, put it behind a reverse proxy that
  adds authentication (e.g. Caddy or nginx with basic auth, or an
  identity-aware proxy) — this project deliberately doesn't invent its own
  auth layer, since "add real auth" is a decision the research team should
  make deliberately, not inherit as a default.
- If you do put a reverse proxy in front of `web` for TLS, only the `web`
  service needs to be reachable from it — `postgres`, `migrate`, and `bot`
  have no listening ports that need to be exposed at all.

## Verifying a deployment

`scripts/verify_production_flow.py` drives one real, complete survey
(including a screenshot upload and a real file write) straight through the
application layer against whatever `DATABASE_URL` you point it at, then —
if you pass `--api-base` — checks that the live web API can actually serve
that survey's detail, its screenshot image, and an Excel export over HTTP.
Useful as a post-deploy smoke test against a staging environment before
trusting a production rollout:

```bash
docker compose exec bot python scripts/verify_production_flow.py --api-base http://web:8000
```

(This creates one real completed survey — don't point it at a database
you need to stay pristine without expecting that.)
