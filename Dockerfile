# Single image shared by all three runtime roles (bot / web / migrate) —
# same codebase, same dependencies, just a different command per
# docker-compose service. Building it once and reusing it is simpler and
# more reliable than maintaining three near-identical Dockerfiles.

FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv==0.11.7

WORKDIR /app

# Dependencies first, in their own layer, so editing application code
# doesn't invalidate the (slow) dependency-install layer on rebuild.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts
COPY docker ./docker
RUN uv sync --frozen --no-dev


FROM python:3.12-slim

RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Screenshot storage lives on a mounted volume in production (see
# docker-compose.yml); create it here too so a local `docker run` without
# a volume still works, owned by the user the process actually runs as.
RUN mkdir -p /data/screenshots && chown -R app:app /data /app && chmod +x /app/docker/entrypoint.sh

USER app

# docker-compose.yml sets an explicit command per service (bot / web /
# one-shot migrate) against this same image, overriding this default —
# it only actually runs on a host with no per-service command of its own
# (e.g. Render), where entrypoint.sh picks bot-vs-web based on $PORT.
CMD ["/app/docker/entrypoint.sh"]
