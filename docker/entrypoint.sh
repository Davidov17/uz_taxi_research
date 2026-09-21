#!/bin/sh
# Default container entrypoint: docker-compose.yml overrides `command:`
# explicitly for each of its services (bot/web/migrate), so this only
# ever runs on a host that *doesn't* set its own command — e.g. Render,
# which has no equivalent to compose's per-service command and instead
# always sets $PORT for a web service. Auto-detecting on that instead of
# requiring a manually-configured "Docker Command" override (easy to miss
# in Render's UI, and the reason this file exists) means a Docker-based
# deploy just works from the Dockerfile's own default CMD.
set -e

if [ -n "$PORT" ]; then
    exec uvicorn telegram_bot.web.app:app --host 0.0.0.0 --port "$PORT"
else
    exec python -m telegram_bot.main
fi
