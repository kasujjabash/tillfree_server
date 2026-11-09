# 03 — Multi-Tenancy Design

## 1. Model

- **Tenant = Organisation.** Every tenant-owned row carries a non-null `organisation_id`.
- A **User** is global (not tenant-owned). Access to an organisation comes only from an active **Membership**.
- **Branch** and **Department** belong to one organisation. Branch-scoped rows carry both `organisation_id` and `branch_id`.
- Shared infrastructure, one PostgreSQL database, one schema, logical isolation.
- The `Organisation` row itself (name, slug, status) is not under RLS: users must see which organisations they belong to before selecting one. Access to it is by membership check; everything sensitive about an organisation lives in tenant tables that are.

Schema-per-tenant was considered and rejected for the default tier: migrations become O(tenants), connection pooling gets harder, and cross-tenant platform operations (billing, support, analytics) become awkward. Database-per-tenant is kept as a future option for large customers (§7).

## 2. Ownership categories

Every table is classified into exactly one category. The category decides its isolation mechanism. The full table-by-table list is in [04-data-model.md](04-data-model.md#2-ownership-and-isolation).

| Category | Examples | Isolation |
|----------|----------|-----------|
| **Global reference** | Country, Currency, Permission, Module, Plan | Readable by all authenticated users; writable only by platform staff. No RLS. |
| **User-owned** | User, AuthSession, MFA device | Accessed only by the user themself or platform identity services. Never via an organisation context. |
| **Tenant** | Organisation settings, Role, Department, audit events, files | `organisation_id` + RLS policy. |
| **Tenant + branch** | Stock, sales, appointments (later) | `organisation_id` + `branch_id` + RLS on organisation; branch restriction enforced by `authorize()` and selectors. |
| **Bridge** | Membership, Invitation | Visible from the organisation side (org context) and from the user side (own rows). RLS policy has both clauses. |

## 3. Four enforcement layers

Isolation does not rely on any single layer. Each layer assumes the one above it might have a bug.

### Layer 1 — Explicit tenant resolution
For tenant-scoped endpoints the client sends `X-Organisation-ID: <uuid>`. The server looks up an **active membership** for `(user, organisation)` with the organisation active. If none exists the response is **404**, not 403, so the endpoint does not confirm that the organisation exists. There is no default organisation, even when the user has only one membership: implicit defaults cause behaviour to change silently when the user later joins a second organisation.

### Layer 2 — Tenant-aware managers
Tenant models use `TenantManager`. Its default queryset **raises `TenantContextMissing`** when no tenant context is set, instead of returning all rows. Code that needs cross-tenant access must use the explicitly named `Model.unscoped` manager, and an automated check fails CI if `unscoped` is used outside an allowlisted set of modules (platform jobs, migrations, the audit exporter).

Related-object lookups from client input (e.g. a `branch` ID in a POST body) are resolved through tenant-scoped querysets, so an ID from another organisation fails validation as "not found".

### Layer 3 — Authorisation in the service layer
`access.authorize(ctx, permission, branch=..., obj=...)` checks permission, module entitlement and branch scope. Services call it; views declare it. Object-level checks always compare `obj.organisation_id == ctx.organisation_id` as well, which also catches bugs in layer 2.

### Layer 4 — PostgreSQL Row-Level Security (backstop)
Even if application code builds an unscoped query, the database returns only the current tenant's rows.

## 4. Row-Level Security in detail

### Roles
| DB role | Used by | Properties |
|---------|---------|------------|
| `tilltree_owner` | Migrations only | Owns tables. Never used by the running app. |
| `tilltree_app` | API and workers | `NOSUPERUSER NOBYPASSRLS`. DML on tenant tables. No DDL. No `UPDATE`/`DELETE` on audit tables. |
| `tilltree_platform` | Allowlisted platform jobs (billing runs, tenant export, retention) | `BYPASSRLS`. Separate Django DB alias (`platform`). Every use audited. |

Tables use `ALTER TABLE … ENABLE ROW LEVEL SECURITY` **and** `FORCE ROW LEVEL SECURITY`, so policies apply even to the table owner. Because `tilltree_owner` is not a superuser, the test suite (which connects as the owner so it can create the test database) is subject to the same policies as production.

Roles are created by `infrastructure/db/create_roles.sql` (local and CI). `tilltree_platform` is added when the first cross-tenant platform job exists (Phase 3).

### Policy
```sql
CREATE POLICY tenant_isolation ON <table>
  USING      (organisation_id = NULLIF(current_setting('app.organisation_id', true), '')::uuid)
  WITH CHECK (organisation_id = NULLIF(current_setting('app.organisation_id', true), '')::uuid);
```
- `current_setting(..., true)` returns NULL when the setting was never defined on the connection, and `''` after a previous transaction on the same pooled connection defined it with `SET LOCAL`. `NULLIF(…, '')` handles both: **no context means no rows** (fail closed), never "all rows".
- `WITH CHECK` prevents inserting or moving rows into another tenant.
- Bridge tables (Membership, Invitation) add an `OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid` clause, so a user can list their own memberships before choosing an organisation.

Policies are created by a custom migration operation (`common.tenancy.operations.EnableTenantRLS("Model")`) so every new tenant table gets the same policy. A test asserts that every model inheriting `TenantModel` has RLS enabled and forced.

### Establishing context and transaction boundaries
The context is set with `set_config('app.organisation_id', <uuid>, true)`. The third argument `true` makes it **transaction-local**: it disappears at `COMMIT` or `ROLLBACK`. Therefore:

1. Every tenant-scoped request runs inside one transaction opened by the base tenant view (`TenantAPIView.dispatch`) after tenant resolution. The context is set as the first statement.
2. Every Celery task that touches tenant data runs inside `with tenant_context(org_id):`, which opens a transaction and sets the context.
3. Because the setting is transaction-local, a pooled connection handed to the next request carries no tenant context. **This is what makes RLS safe with connection pooling** (Django's psycopg pool or PgBouncer in transaction mode). Session-level `SET` is never used.
4. Code that runs outside a transaction (autocommit) has no tenant context and sees no tenant rows. That is intentional.

Trade-off: each tenant request holds a transaction for its full duration. Views must not perform slow external calls (email, SMS, payment APIs) inside a request; those go to Celery after commit. This rule is good practice anyway.

### Things RLS does not cover, handled elsewhere
- Branch-level restrictions (enforced in layer 3 and selectors; branch RLS would make policies too complex to reason about).
- Redis cache keys, object-storage keys, search indexes, exported files (see §6).
- Django admin. Django admin is limited to global reference data and platform configuration. Tenant data is not registered in admin; platform support uses audited support-access endpoints instead.

## 5. Background jobs

Every task that touches tenant data takes `organisation_id` as an explicit argument (a UUID string), never as an implicit global. A Celery base task class:
- Requires `organisation_id` for tasks marked `tenant_task=True` and fails fast without it.
- Wraps execution in `tenant_context()` (transaction + `set_config`).
- Propagates `request_id` and actor ID through task headers for log correlation and audit.

Tasks that iterate over tenants (e.g. nightly expiry alerts) enumerate organisation IDs with the platform alias and dispatch one task per organisation. One task never processes two tenants in one transaction.

## 6. Isolation beyond the database

| Surface | Rule |
|---------|------|
| Cache | Keys built only via `tenant_cache_key(org_id, …)`. Authenticated API responses send `Cache-Control: private, no-store` by default. |
| Object storage | Keys prefixed `org/<org_id>/…`. Bucket private. Downloads via short-lived signed URLs issued after an authorisation check. |
| Exports and reports | Generated by a tenant task; stored under the tenant prefix; downloadable only through the documents domain. |
| Notifications | Recipient list resolved within the tenant; templates rendered with tenant data only. |
| Integrations / webhooks | Credentials stored per organisation; inbound webhooks resolve the tenant from a per-organisation endpoint secret, never from the payload alone. |
| Logs | Contain `organisation_id` and IDs, never tenant payloads. |

## 7. Path to dedicated infrastructure for large customers

Not built now, but nothing in the design prevents it:
- UUID keys mean a tenant's rows can be copied to another database without key collisions.
- Every tenant row keeps `organisation_id`, even in a dedicated database, so the same code and policies work unchanged.
- No foreign keys from one tenant's rows to another's. Global reference data is replicated per database.
- A Django database router keyed on organisation ID (`Organisation.database_alias`, default `"default"`) selects the database. The router is the only code that changes.
- Users and memberships stay in the shared "control plane" database; tenant data moves.

## 8. Required tests

A `tests/security/test_tenant_isolation.py` suite, parametrised over every tenant-scoped endpoint discovered from the URL router:
- Org A member requesting Org B's object by ID → 404.
- Org A member sending `X-Organisation-ID: B` → 404.
- Creating an object in Org A that references an Org B branch/object → 400 "not found".
- Listing never returns Org B rows.
- Raw ORM query without context inside a transaction returns zero rows (RLS backstop).
- A pooled connection reused after an Org A transaction has no residual context.
- A Celery task without `organisation_id` fails.
- Every `TenantModel` has RLS enabled and forced (schema introspection test).
