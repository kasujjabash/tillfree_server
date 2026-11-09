# ADR-0002: Shared-schema multi-tenancy with Row-Level Security

- Status: Accepted (2026-11-09)
- Date: 2026-11-09

## Context
Many organisations share the platform. A cross-tenant data leak is the most damaging failure the platform can have, especially with healthcare data. Some enterprise customers may later require dedicated databases.

## Decision
- One database, one schema; every tenant row has `organisation_id`.
- Enforcement in four layers: explicit tenant resolution from `X-Organisation-ID` + membership; `TenantManager` that raises without context; `authorize()` with object org check; PostgreSQL RLS with `FORCE ROW LEVEL SECURITY`.
- Tenant context set with transaction-local `set_config(..., true)` inside a per-request / per-task transaction; policies use `NULLIF(current_setting(..., true), '')` so missing context yields no rows.
- The app connects as a non-owner role without `BYPASSRLS`; cross-tenant platform jobs use a separate `BYPASSRLS` alias from an allowlisted set of modules.

Details: [03-multi-tenancy.md](../03-multi-tenancy.md).

## Consequences
- Isolation bugs in application code are caught by the database instead of leaking data.
- Each tenant request runs in a transaction; slow external calls must be moved to Celery.
- Migrations run as a separate owner role; developers need to understand the roles.
- Transaction-local settings work with Django's pool and PgBouncer transaction mode.

## Alternatives
- Application filtering only: one missed filter leaks data. Rejected as the sole control.
- Schema per tenant (`django-tenants`): migrations scale with tenant count, cross-tenant platform queries are hard, pooling is harder. Rejected for the default tier.
- Database per tenant: highest isolation and cost; kept as a later option for large customers via a DB router.
