# 10 — Roadmap, Risks and Open Decisions

## 1. Phased roadmap

Each phase ends with its exit criteria met, tests passing in CI, and documentation updated. A phase is not done because endpoints return 200.

### Phase 1 — Foundation ✅ implemented 2026-11-09
**Builds:** `pyproject.toml` + `uv.lock`; settings split (`base/development/testing/production`) with environment validation; Dockerfile (multi-stage, non-root) and Compose (postgres with RLS roles, redis, migrate, api, worker, beat); Celery app; `common/` (UUIDv7, `TimeStampedModel`, `TenantModel` + `TenantManager` + `tenant_context` + RLS migration operation + `TenantTask` + deploy checks, minimal `Organisation` tenant root, problem+json exception handler, cursor pagination, request-ID + structlog, health endpoints); custom `User` model (email login, phone, Argon2id); Ruff, mypy, import-linter, pytest config; CI pipeline; README with setup/run/test instructions.
**Removed:** the original MVP apps. They remain in git history at commit `69fdfdb` for the Phase 4 port ([ADR-0006](adr/0006-existing-mvp-disposition.md)).
**Deferred:** `RequestContext` and the tenant base view move to Phase 2, where memberships exist to build them from.
**Exit:** `docker compose up` gives a running API with `/health/ready/` green; the test suite proves RLS fails closed on a sample tenant table and that tenant context does not survive connection reuse; CI green.

### Phase 2 — Identity, organisations, access, audit
**Builds:** registration, email verification, login/refresh/logout, sessions list/revoke, password reset, TOTP MFA, throttling; organisations, settings, branches, departments, memberships, invitations; permissions registry, role templates, custom roles, branch-scoped assignments, `authorize()`; platform staff + support-access grants; audit log.
**Exit:** cross-tenant, IDOR, privilege-escalation, branch-restriction and token (expired/revoked/reused) security suites pass; OpenAPI documents permissions per endpoint.

### Phase 3 — Platform services
**Builds:** localisation reference data and country configuration (seeded for UG, KE, TZ, RW as a start); `Money`; modules, plans, subscriptions, entitlements, usage limits (entitlement enforcement in `authorize()`); notifications (email adapter, one SMS adapter, in-app, templates, preferences, retries); documents (presigned upload/download, scan status); transactional outbox.
**Exit:** a module can be enabled/disabled per organisation and the backend enforces it; notifications deliver with retries and tenant isolation; file access security tests pass.

### Phase 4 — Shared operational capabilities
**Builds:** people/workforce (staff profiles linked to memberships), customers, inventory (porting the existing FEFO batch and stock movement logic), procurement/suppliers, billing foundations (tenant invoices, payments, one mobile-money provider adapter), reporting foundations (async exports, daily aggregates), workflows only if two concrete use cases need them.
**Exit:** load tests meet targets on inventory and billing endpoints; idempotency on money/stock operations.

### Phase 5 — Industry experiences
**Business:** products/services, sales/POS, invoicing, expenses, financial reports — on top of shared inventory, customers and billing. The current pharmacy retail behaviour (prescription-only items, FEFO, public catalogue) becomes a configuration of this module.
**Healthcare:** only after a clinical advisory process: patient registration and consent, appointments, care-team assignment, then encounters, lab and pharmacy. Clinical workflows are specified and signed off by qualified healthcare professionals before they are built.

## 2. Requirements challenged

| Requirement | Concern | Recommendation |
|-------------|---------|----------------|
| Separate `memberships`, `permissions`, `countries`, `core` apps | Creates dependency cycles and a dumping-ground app | Merge as in [02](02-project-structure.md) |
| "Consistent response structures" interpreted as a success envelope | Duplicates HTTP status, adds noise to every client | No envelope; consistent pagination and problem+json errors |
| Generic workflow engine | Very costly to build well; usually wrong without real use cases | Explicit state machines per domain; extract a shared pattern only after two real cases |
| Kubernetes / microservices readiness from day one | Operational cost for a small team | Managed containers; import-linter boundaries make later extraction possible |
| ASGI deployment | Sync Django under ASGI can be slower | Deploy ASGI as asked, benchmark against WSGI in Phase 1 |
| Default organisation when user has one membership (existing code) | Behaviour changes silently when user joins a second organisation | Always require `X-Organisation-ID` |
| Server-side date/number formatting in API | Couples API to presentation, harder to cache | API returns raw values; server formats only documents and notifications |
| Healthcare in the initial scope | Regulatory, clinical-safety and data-residency obligations are heavy | Design boundaries now (done here); build only after clinical and legal sign-off |

## 3. Risks and gaps

| Risk / gap | Impact | Mitigation |
|-----------|--------|-----------|
| **Scope:** an "operating system for every industry" can absorb years of platform work before any customer gets value | High | Choose one launch vertical and let it pull platform features. Phases 1–3 are kept minimal for that reason. |
| **Data residency:** Uganda's DPPA restricts processing personal data outside Uganda without adequate protection or consent; health data is special personal data. Major cloud providers have no region in East Africa (nearest are in South Africa). | High | Legal advice before choosing hosting. The vendor-neutral container design allows a local data centre or regional provider. |
| **Offline operation:** shops and clinics in the target markets often have unreliable connectivity; a POS that stops when the network drops is not usable | High | Not in the spec. If required, it shapes the API now: client-generated UUIDv7 IDs, idempotent writes, sync endpoints with conflict rules. Decision needed before Phase 4. |
| RLS operational complexity (migrations role, debugging "missing rows") | Medium | Encapsulated in `common/tenancy`; clear errors when context missing; local dev uses same roles |
| Django admin bypasses tenant enforcement | High | Admin restricted to global/platform models; test asserts no tenant model is registered |
| Mobile money and fiscal invoicing integrations (e.g. MTN MoMo, Airtel Money, URA EFRIS) are country-specific and have their own certification processes | Medium | Provider interfaces; integration work scheduled per market |
| SMS costs and deliverability; OTP fraud | Medium | SMS as an optional channel, per-org quotas, rate limits |
| Right to erasure vs audit/financial retention | Medium | Pseudonymisation of personal fields; retention schedule per data category, agreed with legal |
| Reporting on the transactional database slows operations | Medium | Async reports, daily aggregates, read replica when needed |
| Small team, wide stack | Medium | Few moving parts, managed services, strong CI |

## 4. Open decisions for the product owner

1. ~~**Existing code and data.**~~ Resolved 2026-11-09: rewrite approved; migration history reset.
2. **Launch vertical and timeline.** Is pharmacy/retail POS (what the original MVP did) the first experience to ship? If so, Phase 4–5 business work can be pulled forward right after Phase 2.
3. **Hosting and data residency.** Which country/provider will host production data? Is legal advice available on the DPPA?
4. **Clients.** Which clients are planned (web app, Android POS, iOS)? Do any need to work offline?
5. **Sensitive role grants.** Should granting a role with `sensitive`/`clinical` permissions need a second Owner's approval, or is audit + notification enough?
6. **Domain name** for public API documentation and error `type` URIs (currently `urn:tilltree:problem:<code>`). Product name resolved: **Tilltree**.
7. **Malware scanning** of uploads: run ClamAV ourselves, use a provider service, or restrict file types for now?
