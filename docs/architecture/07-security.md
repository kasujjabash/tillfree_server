# 07 — Security Architecture and Threat Model

## 1. Controls by layer

| Layer | Controls |
|-------|----------|
| Edge | TLS 1.2+ only, HSTS (preload once stable), request body limit (10 MB API; uploads go directly to object storage), upstream timeouts (30 s), IP-level rate limits for abuse |
| Application | `SecurityMiddleware` headers (HSTS, `nosniff`, `Referrer-Policy`, `X-Frame-Options: DENY`), CSP on any HTML (admin, API docs), CORS with an explicit origin allowlist, CSRF on cookie-authenticated endpoints (refresh, admin), DRF throttles, problem+json errors with no stack traces, `DEBUG=False` enforced by settings check |
| Authentication | Argon2id, short-lived access tokens, rotating refresh tokens with reuse detection, revocation, throttles + progressive delay, MFA, step-up for sensitive actions ([05](05-identity-and-access.md)) |
| Authorisation | Tenant resolution, tenant managers, `authorize()` with entitlement + branch scope, object policies, RLS ([03](03-multi-tenancy.md)) |
| Input | DRF serializers with explicit fields (never `fields = "__all__"`), size limits on strings/lists, ORM only (raw SQL requires review and parameterisation), server-generated IDs and timestamps |
| Output | Dedicated output serializers per audience (e.g. a cost-price field exists only on serializers used with `inventory.cost.view`), sensitive fields omitted not nulled |
| Data | Encryption at rest (managed disk/DB/bucket encryption), encrypted backups in a separate account, secrets in a secrets manager (never in images or Git), `tillfree_app` DB role with least privilege, audit tables append-only |
| Outbound | SSRF-safe HTTP client for all integrations/webhooks: HTTPS only, DNS resolution checked against private/link-local/metadata ranges, no redirects to internal ranges, timeouts, response size cap |
| Supply chain | Locked dependencies (`uv.lock`), `pip-audit` + Dependabot/Renovate in CI, minimal base image, non-root container user, image scanning |
| Logging | Structured logs with IDs only; a redaction filter drops keys like `password`, `token`, `authorization`, `secret`, `otp`, and any field classified as clinical |

## 2. File handling

1. Client asks `POST /api/v1/files/uploads` with name, size and type. Server authorises, checks size/type allowlist and the tenant's storage quota, and returns a **presigned POST** limited to that key, size and content type, valid for 5 minutes.
2. Client uploads directly to object storage under `org/<org_id>/uploads/<uuid>` (the original filename is metadata only, never part of the key).
3. Client confirms; a worker verifies size and SHA-256, sniffs the real content type, and runs a malware scan. The file is `pending` until clean; pending/infected files cannot be downloaded.
4. Downloads: authorisation check through the owning domain's policy, then a presigned GET valid for 60 s with `Content-Disposition: attachment`.

Malware scanning (e.g. ClamAV in a worker container) is an open decision on cost; until decided, uploads are restricted to images and PDFs.

## 3. Audit log

- `audit.record(ctx, action, resource, metadata)` is called by services inside the same transaction as the change.
- Recorded: authentication events, membership and role changes, entitlement changes, support-access use, data exports, deletions, and every read of a `clinical` resource.
- Append-only: the application DB role has no `UPDATE`/`DELETE` privilege on the table. Monthly partitions; retention per data category (proposal: 7 years for financial, as required by law per country).
- Metadata passes through an allowlist-based redactor. No passwords, tokens, OTPs, clinical content or full request bodies.
- Optional later: hash chaining per partition for tamper evidence, and streaming to separate write-once storage.

## 4. Healthcare data (designed now, built in Phase 5)

- Clinical data lives only in `industries/healthcare` tables, every one classified `clinical`.
- Clinical permissions are never implied by Owner/Admin roles; clinical roles are assigned explicitly, audited, and require MFA.
- Object-level policy: access tied to care relationship (care team, assignment, or encounter) rather than "any clinician in the organisation". Emergency (break-glass) access with mandatory reason and after-the-fact review.
- All clinical reads and writes audited.
- Consent and legal basis recorded per patient and purpose. Data minimisation in notifications (SMS says "You have an appointment tomorrow", never the clinical reason). Clinical data excluded from analytics unless de-identified.
- Field-level encryption (envelope encryption with a KMS key) only for a small set of high-risk fields (e.g. national ID numbers, specific diagnoses) once identified with clinical and legal advisers. It makes those fields unsearchable, so it is applied deliberately, not everywhere.
- Designed with the Uganda Data Protection and Privacy Act 2019 in mind (health data is special personal data; registration with the Personal Data Protection Office; data subject rights; breach notification; restrictions on processing outside Uganda). **Hosting location is therefore a legal question as well as a technical one** — see open decisions. A legal review is required before handling real patient data.

## 4a. Decisions made while implementing Phase 2

- **Rate limits fail closed.** Throttle and brute-force counters live in Redis. If Redis is unreachable, throttled endpoints return 500 rather than silently dropping brute-force protection; the readiness probe reports the instance unavailable so the load balancer stops routing to it.
- **Emails never break a committed request.** They are queued after commit with `robust=True`; a broker outage is logged and alerted, and the user's action still succeeds.
- **Tracebacks in logs never include local variables** (structlog's default would), so secrets in a crashing function cannot reach log storage.
- **Owners cannot lock everyone out:** enabling "require two-step verification" for an organisation is refused unless the person enabling it is signed in with two-step verification.
- **Account enumeration is avoided** on registration, resend-verification, forgot-password and email-change: responses are identical whether or not the address exists; only the inbox learns the difference.

## 5. Threat model

STRIDE-style, prioritised. "L" = likelihood, "I" = impact (H/M/L).

| # | Threat | Area | L | I | Mitigations | Verified by |
|---|--------|------|---|---|-------------|-------------|
| T1 | Member of Org A reads/modifies Org B data via guessed or leaked IDs (IDOR) | Tenant isolation | H | H | UUIDs, explicit tenant resolution → 404, tenant managers that raise without context, object org check, RLS backstop | Parametrised cross-tenant suite over all endpoints; RLS introspection test |
| T2 | Tenant context leaks between requests on a pooled connection | Tenant isolation | M | H | Transaction-local `set_config` only; no session `SET`; fail-closed `NULLIF` policy | Pool-reuse test |
| T3 | Background job processes data without or with wrong tenant | Tenant isolation | M | H | Mandatory `organisation_id` arg, `tenant_context()` wrapper, one tenant per task | Task base-class tests |
| T4 | Cross-tenant data through cache, exports or file URLs | Tenant isolation | M | H | Tenant-prefixed cache keys via helper only, tenant storage prefix, short signed URLs after authz | Unit tests on helpers; file-access security tests |
| T5 | Credential stuffing / brute force | Authentication | H | M | Throttles per IP and account, progressive delay, breached-password check, MFA, login audit | Rate-limit tests |
| T6 | Stolen refresh token | Authentication | M | H | Rotation + family revocation on reuse, HttpOnly cookie, session list + remote revoke | Token reuse tests |
| T7 | Account takeover via password reset or SIM swap | Authentication | M | H | Hashed single-use short-lived reset tokens, uniform responses, session revocation on reset, SMS not sole factor for privileged users | API tests |
| T8 | Staff escalates own privileges (assigns self a higher or clinical role) | Privileged users | M | H | Grant-only-what-you-hold, scope containment, last-owner rule, audit + owner notification, MFA step-up | Privilege-escalation test suite |
| T9 | Organisation admin browses clinical/sensitive data | Privileged users | M | H | Sensitivity classes not in admin templates, clinical read audit, care-relationship policy | Authorisation tests |
| T10 | Platform staff or compromised staff account accesses tenant data | Privileged users | L | H | No default tenant access, owner-approved time-boxed grants, mandatory MFA, break-glass with notification, no tenant data in Django admin | Support-access tests; admin registration test |
| T11 | Malicious upload (malware, HTML/SVG XSS, zip bomb, oversized) | Files | M | M | Type allowlist, sniffing, size limits at presign, scan before availability, attachment disposition, separate storage domain | File security tests |
| T12 | API abuse: scraping, enumeration, expensive queries, DoS | API abuse | H | M | Throttles (anon/user/tenant), pagination caps, filter allowlists, query timeouts (`statement_timeout`), report generation async | Rate-limit and pagination tests; load tests |
| T13 | Duplicate payment/stock operation from retries | API abuse | H | M | `Idempotency-Key` on money/stock POSTs, DB uniqueness on external references | Idempotency tests |
| T14 | SSRF through webhook URLs or integration endpoints | Integrations | M | H | SSRF-safe client, allowlisted schemes, private-range blocking after DNS resolution | Unit tests with private addresses |
| T15 | Forged inbound webhooks (e.g. fake payment confirmations) | Integrations | M | H | Per-provider signature verification, per-org secrets, verify status with provider API before crediting | Integration tests |
| T16 | Clinical or personal data in logs, error reports, notifications, analytics | Healthcare / privacy | M | H | Redaction filter, Sentry `before_send` scrubbing + `send_default_pii=False`, notification minimisation, clinical excluded from analytics | Log redaction tests |
| T17 | Audit log tampering | Integrity | L | H | No UPDATE/DELETE grants, partitioning, optional hash chain / external sink | DB privilege test |
| T18 | Secrets leaked in repo/images | Supply chain | M | H | Secrets manager, `.env` ignored, secret scanning in CI, production settings refuse dev defaults | CI checks |
| T19 | Vulnerable dependency | Supply chain | M | M | Lock file, `pip-audit`, update bot, pinned base images | CI |
| T20 | Data loss / ransomware | Availability | L | H | PITR, encrypted off-account backups, quarterly restore tests | Restore runbook record |

This model is reviewed at the end of every phase and whenever a new external integration or data class is added.
