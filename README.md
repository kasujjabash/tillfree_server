# Tillfree — backend

**The Operating System for Organisations.**
One platform. Every operation. Room to grow.

A multi-tenant Django modular monolith serving organisations across industries and countries. Architecture, decisions and roadmap: [docs/architecture](docs/architecture/README.md).

**Status:** Phase 1 (foundation) complete — project structure, settings, tenancy machinery with PostgreSQL row-level security, custom user model, error contract, logging, health checks, Celery, Docker, CI. Phase 2 (authentication, organisations, roles) is next.

## Stack

Python 3.14 · Django 5.2 LTS · Django REST Framework · PostgreSQL 17+ · Redis · Celery · Gunicorn + Uvicorn (ASGI) · uv · Ruff · mypy · import-linter · pytest

## Layout

```
config/         settings (base, development, testing, production), urls, asgi/wsgi, celery
common/         framework-level code, no domain logic:
  tenancy/        tenant_context, TenantModel/TenantManager, RLS migration operation, TenantTask, deploy checks
  api/            problem+json errors, cursor pagination
  observability/  structured logging, redaction, request IDs, Sentry scrubbing
  db/ health/     UUIDv7 base models; liveness/readiness endpoints
apps/           business domains
  identity/       custom User (email login, E.164 phone, Argon2id)
  organisations/  Organisation (tenant root)
tests/          cross-cutting suites: security/ (tenant isolation), api/; tenancy_app/ is a test-only tenant model
infrastructure/ db/create_roles.sql, docker/ (gunicorn config, postgres init)
docs/           architecture and ADRs
```

## Local development without Docker

Requires Python 3.14, [uv](https://docs.astral.sh/uv/) and a local PostgreSQL 17+ with a superuser. Redis is optional: without it, the cache is in memory and Celery tasks run inline.

```bash
uv sync                                   # create .venv with all dependencies
cp .env.example .env
make db-roles                             # creates database `tillfree`, roles tillfree_owner / tillfree_app
make migrate
uv run python manage.py createsuperuser   # platform staff account for /admin/
make run                                  # http://localhost:8000/health/ready/  ·  /api/docs/
```

`make db-roles` uses your OS user as the PostgreSQL superuser; override with `make db-roles DB_SUPERUSER=postgres`.

Locally the app connects as `tillfree_owner` for simplicity. That role is not a superuser and every tenant table uses `FORCE ROW LEVEL SECURITY`, so isolation is still enforced. In Docker and production the app connects as `tillfree_app`, which cannot change the schema.

## Local development with Docker

```bash
cp .env.example .env
docker compose up --build
```

Services: `postgres`, `redis`, `migrate` (runs once as `tillfree_owner`), `api` on :8000 (as `tillfree_app`), `worker`, `beat`.

## Quality checks

```bash
make test        # pytest with coverage (needs PostgreSQL; the test DB is created automatically)
make lint        # ruff lint + format check
make typecheck   # mypy (django-stubs, drf-stubs)
make imports     # import-linter domain-boundary contracts
make check       # fails if a model change has no migration
make ci          # all of the above
```

CI (`.github/workflows/ci.yml`) runs the same checks against PostgreSQL 17 with the production role model, audits dependencies with pip-audit and builds the production image.

## Conventions that matter from day one

- **Tenant data:** inherit `common.tenancy.models.TenantModel`, add `EnableTenantRLS("<Model>")` to the migration, and access it only inside `tenant_context(org_id)` (requests, Celery `TenantTask`, commands). Tests fail if a tenant table lacks RLS.
- **Never register tenant models in Django admin.** Admin bypasses tenant enforcement; a test enforces this.
- **Errors:** raise `common.exceptions.BusinessRuleViolation` (or a subclass) from services; the API returns RFC 9457 `application/problem+json`.
- **Time:** store UTC; compute business dates in the branch or organisation timezone, never the server's.
- **Boundaries:** `common` never imports `apps` or `config`; `apps` never import `config`. Checked by `make imports`.

## Production deployment notes

- Image: `docker build --target production .` (non-root, Gunicorn + `uvicorn-worker`, static files via WhiteNoise).
- Run migrations as a one-off job with the owner role (`DB_USER=tillfree_owner`, `DB_STATEMENT_TIMEOUT_MS=0`), then roll out the API and workers with `DB_USER=tillfree_app`.
- After migrating, run `python manage.py check --deploy --database default` as the app role. It fails if the role can bypass row-level security or if any tenant table lacks RLS.
- Required settings: `DJANGO_SECRET_KEY` (≥ 50 random chars), `DJANGO_ALLOWED_HOSTS`, database, `REDIS_CACHE_URL`, `CELERY_BROKER_URL`. See `.env.example`.
- Probes: liveness `GET /health/live/`, readiness `GET /health/ready/` (database and cache).
