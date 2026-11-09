# 08 — API Architecture and Conventions

## 1. Versioning and layout

- URL versioning: `/api/v1/…`. A new major version only for breaking changes; additive changes (new fields, endpoints) stay in v1. Deprecations announced with a `Deprecation` and `Sunset` header.
- Endpoints grouped by domain:

| Prefix | Context | Examples |
|--------|---------|----------|
| `/api/v1/auth/` | Public / user | `login`, `refresh`, `logout`, `password/reset`, `verify-email`, `mfa/…` |
| `/api/v1/me/` | User (no tenant header) | profile, sessions, memberships, notification preferences |
| `/api/v1/organisations/` | User to create; tenant for the current org | create, current org profile, settings |
| `/api/v1/branches/`, `/departments/`, `/memberships/`, `/invitations/`, `/roles/` | Tenant | |
| `/api/v1/permissions/`, `/modules/`, `/countries/`, `/currencies/` | Reference (authenticated) | |
| `/api/v1/subscriptions/`, `/entitlements/` | Tenant | |
| `/api/v1/notifications/`, `/files/`, `/audit-events/` | Tenant | |
| `/api/v1/platform/…` | Platform staff + MFA | organisations, plans, support grants |
| `/health/live/`, `/health/ready/` | Infrastructure | outside `/api` |

- OpenAPI 3.1 schema via **drf-spectacular** at `/api/schema/`; Swagger UI at `/api/docs/` (disabled or authenticated in production). Every endpoint documents its required permission and whether it needs `X-Organisation-ID` (added automatically from the view's `required_permissions` by a spectacular extension).

## 2. Requests

| Header | Purpose |
|--------|---------|
| `Authorization: Bearer <access>` | Authentication |
| `X-Organisation-ID: <uuid>` | Active organisation, required on tenant endpoints |
| `Idempotency-Key: <uuid>` | Required on operations that create money or stock effects; optional elsewhere |
| `Accept-Language` | Message language |
| `X-Request-ID` | Optional; echoed back, generated if absent |

JSON only, `snake_case` fields, UUIDs as strings, timestamps ISO 8601 UTC, dates `YYYY-MM-DD`, money as `{"amount": "…", "currency": "…"}`.

## 3. Responses

**No envelope for success.** HTTP status codes already say whether the call succeeded, and wrapping every body in `{"success": true, "data": …}` duplicates that.

- Single resource: the object.
- Collections: always paginated.
  ```json
  { "results": [ … ], "next": "<cursor or null>", "previous": "<cursor or null>" }
  ```
  Default **cursor pagination** (stable under inserts, efficient on large tables, ordered by `(created_at, id)`); page-number pagination only on small reference lists. Default page size 25, maximum 100.
- `201` with the created object; `204` for deletes; `202` + job resource for async work (exports, reports).

## 4. Errors — RFC 9457 Problem Details

`Content-Type: application/problem+json`, produced by one global DRF exception handler for every error, including 404/405/500:

```json
{
  "type": "urn:tillfree:problem:validation_error",
  "title": "Invalid input.",
  "status": 400,
  "code": "validation_error",
  "detail": "One or more fields are invalid.",
  "request_id": "0193…",
  "errors": [
    { "field": "branch", "code": "does_not_exist", "message": "Branch not found." },
    { "field": "items[2].quantity", "code": "min_value", "message": "Must be at least 1." }
  ]
}
```

| Status | `code` examples |
|--------|-----------------|
| 400 | `validation_error`, `business_rule_violation` (e.g. `insufficient_stock` as nested code) |
| 401 | `not_authenticated`, `token_expired`, `session_revoked` |
| 403 | `permission_denied`, `module_not_enabled`, `mfa_required`, `branch_not_permitted` |
| 404 | `not_found` (also for other tenants' resources and non-member organisations) |
| 409 | `conflict`, `idempotency_key_reused` |
| 422 | not used (DRF convention is 400) |
| 429 | `rate_limited` with `Retry-After` |
| 500 | `server_error` with `request_id` only, no internal detail |

Domain services raise typed exceptions (`common.exceptions.BusinessRuleViolation(code=…)`); the handler maps them. Views do not build error responses by hand.

## 5. Filtering, search, sorting

- `django-filter` FilterSets with explicit allowlisted fields; `?search=` on declared fields (PostgreSQL trigram/full-text where volume requires); `?ordering=` on declared fields only.
- Date ranges as `?created_after=` / `?created_before=` (ISO), interpreted in the branch/org timezone when the field is a business date.
- Unknown filter parameters are ignored, not errors, but an over-limit `page_size` is clamped.

## 6. Idempotency

`IdempotencyRecord(organisation_id, user_id, key, request_fingerprint, response_status, response_body, created_at)` with a unique constraint on `(organisation_id, user_id, key)`, stored in PostgreSQL inside the same transaction as the operation. A repeat with the same key and same body returns the stored response; same key with a different body returns `409 idempotency_key_reused`. Records expire after 24 h. This also supports clients that retry after a network drop, which matters on mobile networks.

## 7. Rate limiting

Redis-backed DRF throttles, layered: anonymous per IP, authenticated per user, per organisation (protects the platform from one noisy tenant), and stricter scopes for auth endpoints. Limits are configuration, not code.

## 8. Contracts

- Input and output serializers are separate where they differ. Models are never exposed as-is; field lists are explicit.
- Nested serializers only one level deep on list endpoints; related details fetched by ID or via an `?expand=` allowlist later if needed.
- The OpenAPI schema is generated in CI and diffed; a breaking change in v1 fails the build.

## 9. Implemented endpoints (Phase 2)

The interactive reference, generated from code, is at `/api/docs/` (Swagger UI) and `/api/schema/` (OpenAPI 3). Every tenant endpoint lists the `X-Organisation-ID` header and its exact permission. Summary:

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `POST /auth/register/`, `/auth/verify-email/`, `/auth/verify-email/resend/` | public | Sign up; confirm email via emailed link |
| `POST /auth/login/` | public | Password sign-in. Returns tokens, or `{mfa_required, mfa_token}` |
| `POST /auth/mfa/verify/` | public | Second step: `mfa_token` + authenticator `code` or `recovery_code` |
| `POST /auth/token/refresh/` | refresh token | Rotate tokens (body token or `tf_refresh` cookie) |
| `POST /auth/logout/`, `/auth/logout-all/` | access token | End this session / every session |
| `POST /auth/password/forgot/`, `/auth/password/reset/`, `/auth/password/change/` | public / public / access | Password recovery and change |
| `POST /auth/email/change/`, `/auth/email/confirm/` | access / public | Change sign-in email (confirmed from the new inbox) |
| `POST /auth/mfa/totp/setup/`, `confirm/`, `disable/`, `/auth/mfa/recovery-codes/` | access | Manage two-step verification |
| `GET/PATCH /me/`, `GET /me/sessions/`, `DELETE /me/sessions/{id}/` | access | Profile and signed-in devices |
| `GET /me/memberships/` | access | Organisations I belong to (pick one, then send its id as `X-Organisation-ID`) |
| `POST /organisations/` | access, verified email | Create an organisation; creator becomes Owner |
| `GET /me/access/` | tenant | My permissions and branch grants here: drives which menus/buttons the UI shows |
| `GET/PATCH /organisations/current/`, `/organisations/current/settings/` | tenant | Profile; security settings (e.g. require two-step verification) |
| `/branches/`, `/departments/` | tenant | Structure; branch-scoped roles see only their branches |
| `/memberships/` (+ `suspend/`, `reactivate/`, `leave/`) | tenant | Team management |
| `/roles/`, `/role-assignments/`, `GET /role-templates/`, `GET /permissions/` | tenant / access | Custom roles and who holds what, where |
| `/invitations/` (+ `resend/`, `revoke/`), `POST /invitations/preview/`, `accept/`, `accept-new/` | tenant / public / access | Invite by email; invitee joins with or without an existing account |
| `/support-grants/` (+ `approve/`, `deny/`, `revoke/`) | tenant | Organisation decides on platform support access |
| `GET /audit-events/` | tenant (`audit.event.view`) | The organisation's audit log |
| `GET /platform/organisations/`, `GET/POST /platform/support-grants/` | platform staff + two-step | Platform operations |

### Typical client flow

1. `POST /auth/login/` → keep `access_token` in memory; mobile keeps `refresh_token` in the secure keystore, web passes `use_cookie: true`.
2. On `401 token_expired`, call `POST /auth/token/refresh/` once and retry. On any other 401, send the user to sign-in.
3. `GET /me/memberships/` → user picks an organisation → send `X-Organisation-ID` on every tenant call.
4. `GET /me/access/` → show only what the user may do. The server still checks every request.
5. `403 mfa_required` → ask the user to set up two-step verification and sign in again.
