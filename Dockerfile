# syntax=docker/dockerfile:1.7
# One image runs every process type: API (gunicorn), worker, beat and the migration job.

ARG PYTHON_VERSION=3.14

FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
WORKDIR /app

# ---- runtime dependencies only (cached unless the lock file changes)
FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

# ---- local development: adds test/lint tools; source is bind-mounted by compose
FROM deps AS dev
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project
COPY . .
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# ---- production
FROM base AS production
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
COPY --from=deps /opt/venv /opt/venv
COPY --chown=app:app . .
# Static files for the admin and API docs, served by WhiteNoise. A throwaway key satisfies
# the production settings checks; no database is touched.
RUN DJANGO_SETTINGS_MODULE=config.settings.production \
    DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
    JWT_SIGNING_KEYS="build:$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
    FIELD_ENCRYPTION_KEYS="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
    DJANGO_ALLOWED_HOSTS=build.invalid \
    python manage.py collectstatic --noinput -v0
USER app
EXPOSE 8000
CMD ["gunicorn", "config.asgi:application", "-c", "infrastructure/docker/gunicorn.conf.py"]
