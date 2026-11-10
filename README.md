# Tillfree — backend

**The Operating System for Organisations.**
One platform. Every operation. Room to grow.

A multi-tenant Django modular monolith serving organisations across industries and countries. Architecture, decisions and roadmap: [docs/architecture](docs/architecture/README.md).

**Status:** Phases 1–3 complete: foundation; identity and access (sign-in with two-step verification, organisations, roles with branch scope, invitations, support access, audit log); platform services (countries and currencies for UG/KE/TZ/RW, money handling, plans and limits, events, in-app/email/SMS notifications, secure file uploads). Phase 4 (shared operations: customers, inventory, billing and finance) is next.

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
apps/           business domains, each: api/ -> services -> models, plus public.py
  audit/          append-only audit log
  identity/       users, sessions, tokens, two-step verification, sign-in/up API
  organisations/  organisation, settings, branches, departments
  access/         memberships, invitations, roles, permissions, authorize(), tenant API base, support access
  localisation/   countries, currencies, tax rates, formatting
  subscriptions/  modules, plans, limits, subscriptions, platform invoices
  events/         transactional outbox and event handlers
  notifications/  in-app inbox, email and SMS delivery, preferences, templates
  documents/      secure uploads to S3-compatible storage, scanning, downloads
tests/          cross-cutting suites: security/ (tenant isolation, cross-tenant API), architecture/, api/
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
make schema      # OpenAPI schema must generate without warnings
make ci          # all of the above
```

CI (`.github/workflows/ci.yml`) runs the same checks against PostgreSQL 17 with the production role model, audits dependencies with pip-audit and builds the production image.

## Using the API

Interactive docs at http://localhost:8000/api/docs/ (development). Typical flow:

```bash
# sign up, then open the verification link printed in the server console
curl -X POST localhost:8000/api/v1/auth/register/ -H 'Content-Type: application/json' \
     -d '{"email":"me@example.com","password":"a-long-passphrase-9","full_name":"Me"}'
curl -X POST localhost:8000/api/v1/auth/login/ -H 'Content-Type: application/json' \
     -d '{"email":"me@example.com","password":"a-long-passphrase-9"}'          # -> access_token
curl -X POST localhost:8000/api/v1/organisations/ -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"name":"My Clinic","country_code":"UG","base_currency":"UGX","default_timezone":"Africa/Kampala"}'
curl localhost:8000/api/v1/branches/ -H "Authorization: Bearer $TOKEN" -H "X-Organisation-ID: $ORG_ID"
```

Full endpoint list and client guidance: [docs/architecture/08-api.md](docs/architecture/08-api.md#9-implemented-endpoints-phase-2). Platform staff are created in the Django admin (`/admin/`, "Platform staff").

## Conventions that matter from day one

- **Tenant data:** inherit `common.tenancy.models.TenantModel`, add `EnableTenantRLS("<Model>")` to the migration, and access it only inside `tenant_context(org_id)` (requests, Celery `TenantTask`, commands). Tests fail if a tenant table lacks RLS.
- **Never register tenant models in Django admin.** Admin bypasses tenant enforcement; a test enforces this.
- **Errors:** raise `common.exceptions.BusinessRuleViolation` (or a subclass) from services; the API returns RFC 9457 `application/problem+json`.
- **Time:** store UTC; compute business dates in the branch or organisation timezone, never the server's.
- **Boundaries:** a domain uses another only through its `public.py`; dependencies point down (access → organisations → identity → audit); inside a domain `api → services → models`. Checked by `make imports` and `tests/architecture/`.
- **Permissions:** declare them in your app's `permissions.py`; every tenant view action must name the permission it needs in `required_permissions` (a test fails otherwise).

## Production deployment notes

- Image: `docker build --target production .` (non-root, Gunicorn + `uvicorn-worker`, static files via WhiteNoise).
- Run migrations as a one-off job with the owner role (`DB_USER=tillfree_owner`, `DB_STATEMENT_TIMEOUT_MS=0`), then roll out the API and workers with `DB_USER=tillfree_app`.
- After migrating, run `python manage.py check --deploy --database default` as the app role. It fails if the role can bypass row-level security or if any tenant table lacks RLS.
- Required settings: `DJANGO_SECRET_KEY` (≥ 50 random chars), `JWT_SIGNING_KEYS`, `FIELD_ENCRYPTION_KEYS`, `DJANGO_ALLOWED_HOSTS`, database, `REDIS_CACHE_URL`, `CELERY_BROKER_URL`, SMTP settings, `FRONTEND_URL`, S3 storage (`S3_*`), a malware scanner (`FILE_SCANNER`, `CLAMD_*`), and the SMS gateway if SMS is used. See `.env.example`.
- Run a Celery beat process (exactly one) for the outbox relay, subscription renewals and upload clean-up.
- Probes: liveness `GET /health/live/`, readiness `GET /health/ready/` (database and cache).
