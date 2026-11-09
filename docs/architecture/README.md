# Tillfree — Backend Architecture

Status: **Approved 2026-11-09. Phase 1 (foundation) implemented**; Phases 2–5 pending. See the [roadmap](10-roadmap-and-risks.md).

| # | Document | Covers |
|---|----------|--------|
| 01 | [Overview](01-overview.md) | High-level architecture, request lifecycle, assessment of the existing code |
| 02 | [Project structure and domains](02-project-structure.md) | Refined Django layout, domain boundaries, dependency rules, cross-domain communication |
| 03 | [Multi-tenancy](03-multi-tenancy.md) | Tenant model, enforcement layers, PostgreSQL Row-Level Security, background jobs, dedicated-database path |
| 04 | [Data model](04-data-model.md) | ER diagram, ownership/isolation per table, key conventions |
| 05 | [Identity and access](05-identity-and-access.md) | Authentication, sessions, tokens, MFA, RBAC, platform staff and support access |
| 06 | [Countries and localisation](06-localisation.md) | Country configuration, money, time, language, regulatory boundaries |
| 07 | [Security](07-security.md) | Security architecture and threat model |
| 08 | [API conventions](08-api.md) | Versioning, response and error formats, pagination, idempotency |
| 09 | [Infrastructure and operations](09-infrastructure.md) | Deployment, performance, caching, background jobs, observability, backups, CI/CD |
| 10 | [Roadmap, risks and open decisions](10-roadmap-and-risks.md) | Phases with exit criteria, risks, challenged requirements, questions for the product owner |
| — | [Architecture decision records](adr/) | One file per significant decision |

## Decisions at a glance

1. **Modular monolith** on Django 5.2 LTS + DRF + PostgreSQL + Redis + Celery. ([ADR-0001](adr/0001-modular-monolith.md))
2. **Shared database, shared schema, `organisation_id` on every tenant row**, enforced three times: tenant-aware managers, service/API permission checks, and PostgreSQL RLS as a fail-closed backstop. ([ADR-0002](adr/0002-tenancy-shared-schema-with-rls.md))
3. **UUIDv7 primary keys** for all domain tables. ([ADR-0003](adr/0003-uuidv7-primary-keys.md))
4. **Short-lived JWT access tokens + server-side, rotating refresh sessions** with reuse detection. Organisation context comes from an explicit request header, never from the token. ([ADR-0004](adr/0004-authentication-tokens.md))
5. **Permissions are code-declared strings; roles are per-organisation data.** Sensitive permissions (e.g. clinical) are never implied by Owner or Admin roles. ([ADR-0005](adr/0005-rbac-model.md))
6. **The original pharmacy MVP was replaced, not extended.** The foundation was rebuilt with a fresh migration history; its inventory and sales logic (commit `69fdfdb`) is ported in Phase 4. ([ADR-0006](adr/0006-existing-mvp-disposition.md))
7. **Errors follow RFC 9457 (problem+json); success bodies are not wrapped in an envelope.** ([08-api.md](08-api.md))
