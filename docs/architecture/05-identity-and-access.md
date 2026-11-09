# 05 — Identity, Authentication and Authorisation

## 1. Identity

- Custom `User` model (`AbstractBaseUser` + `PermissionsMixin` only for Django admin compatibility), **email as login identifier** (case-insensitive: stored lower-case, enforced by a check constraint plus a unique index), optional unique phone in E.164 (validated with `phonenumbers`).
- No username. No organisation fields on the user.
- Password hashing: **Argon2id** (`argon2-cffi`) as the primary hasher; Django's validators plus a minimum length of 10 and a breached/common-password check.
- Email verification required before an account can accept an invitation or create an organisation. Phone verification by SMS OTP is supported but optional, because SMS costs money per message and is vulnerable to SIM-swap; it is not used as the sole factor for high-privilege accounts.
- Verification, password-reset and invitation tokens are random 256-bit values, stored **hashed**, single-use, short-lived (reset: 30 min; verification: 48 h; invitation: 7 days).
- Password reset and "forgot password" responses are identical whether or not the account exists. A successful reset revokes all sessions.

## 2. Sessions and tokens

[ADR-0004](adr/0004-authentication-tokens.md) records the decision. Summary:

| Token | Form | Lifetime | Storage |
|-------|------|----------|---------|
| Access token | Signed JWT: `sub` (user id), `sid` (session id), `exp`, `iat`, `jti`. **No organisation, no roles, no PII.** | 10 minutes | Client memory |
| Refresh token | Opaque random 256-bit string | 30 days sliding, 90 days absolute | Server: SHA-256 hash in `AuthSession`. Web client: `HttpOnly; Secure; SameSite=Strict` cookie scoped to `/api/v1/auth/`. Mobile: secure OS keystore. |

- **Rotation with reuse detection:** every refresh issues a new refresh token and invalidates the old one. If an old token is presented again, the whole token family is revoked (it was stolen or replayed).
- **Revocation:** logout revokes the session; "log out everywhere" revokes all sessions; deactivating a user, resetting a password, or removing MFA revokes all sessions. Each request checks that the access token's `sid` is not revoked, using a Redis set of revoked session IDs (entries expire after the access-token lifetime). Worst-case revocation delay is the cache round trip, not the token lifetime.
- **Sessions list:** users can see and revoke their own sessions (device label, created, last used, approximate location from IP).
- Django session authentication is enabled only for the Django admin (platform staff).

Why not plain `djangorestframework-simplejwt` with its blacklist? It works, but the blacklist is keyed on token rather than on device session, does not do family reuse detection, and does not give a session list. A small `AuthSession` model gives all three with little code. `PyJWT` is used for signing; signing keys are rotated using `kid`.

## 3. Brute-force protection

- Per-IP and per-account throttles on login, refresh, reset, verification and OTP endpoints (Redis-backed DRF throttles).
- Per-account failed-attempt counter with **progressive delay** (exponential backoff), not a hard lockout, so an attacker cannot lock a victim out on purpose. After a threshold, require MFA or email confirmation for the next login.
- Generic error messages ("Invalid email or password").
- Login events (success, failure, MFA challenge, lockout) are written to the audit log, without passwords or tokens.

## 4. Multi-factor authentication

- TOTP (authenticator apps) in Phase 2, with hashed one-time recovery codes. WebAuthn/passkeys later.
- MFA is **mandatory for platform staff** and can be made mandatory per organisation (setting) or per role (e.g. any role holding `sensitive` or `clinical` permissions).
- Step-up: sensitive actions (changing roles, exporting data, disabling MFA) require an MFA check within the last N minutes, carried as `amr`/`auth_time` claims.

## 5. Organisation context

1. After login, the client calls `GET /api/v1/me/memberships` to list organisations.
2. For tenant-scoped calls the client sends `X-Organisation-ID`.
3. The server resolves the membership on every request (cached per `(user, org)` for 60 s, invalidated on membership/role change) and builds `RequestContext`.
4. Different browser tabs can work in different organisations at the same time, because context is per request, not per session.

## 6. Authorisation model (RBAC with scopes)

### Concepts
| Concept | Defined by | Example |
|---------|-----------|---------|
| **Permission** | Code. Each module declares its permissions in `permissions.py`; a deploy step syncs them into the `Permission` table. | `organisations.branch.create`, `inventory.stock.adjust`, `healthcare.clinical_note.read` |
| **Sensitivity** | Declared with the permission | `standard`, `sensitive` (payroll, cost prices, exports), `clinical` |
| **Role** | Data, per organisation. Platform ships **templates** (Owner, Administrator, Manager, Staff Member); modules ship their own templates (e.g. Pharmacist, Clinician). Organisations may create custom roles. | "Branch Manager — Kampala" |
| **Role assignment** | Membership × Role × optional Branch | Manager at Branch A, Staff Member at Branch B |
| **Entitlement** | Subscription / override | `healthcare.clinical` module enabled |

### Decision procedure: `authorize(ctx, permission, *, branch=None, obj=None)`
1. Membership is active and organisation is active (already guaranteed by context resolution).
2. The permission's module is entitled for the organisation. Otherwise **403 `module_not_enabled`**.
3. Some role assignment grants the permission, **and** its branch scope is null (all branches) or equals `branch` (or `obj.branch_id`). For list endpoints the same rule yields the set of permitted branch IDs, which selectors use as a filter.
4. If `obj` is given: `obj.organisation_id == ctx.organisation_id`, plus any object policy the owning domain registers (e.g. "a clinician may read notes only for patients assigned to their care team").
5. Denials of `sensitive`/`clinical` permissions are audited. Successful **reads** of `clinical` resources are audited too.

The effective permission set for `(membership)` is computed once and cached in Redis under a tenant-scoped key with a version number that is bumped whenever roles, role assignments or entitlements change.

Views declare permissions per action:
```python
class BranchViewSet(TenantViewSet):
    required_permissions = {
        "list": "organisations.branch.view",
        "create": "organisations.branch.create",
        "partial_update": "organisations.branch.update",
    }
```
A test fails if any tenant view action lacks a declared permission. Permission checks never appear as ad-hoc `if role == ...` in views.

### Guardrails against privilege escalation
- **Owner and Administrator templates do not include `clinical` permissions**, and do not include `sensitive` ones beyond what their template lists. Getting clinical access requires an explicit assignment of a clinical role.
- A member can grant only permissions they themselves hold, and can grant roles only at branch scopes they themselves cover.
- Granting a role that contains `sensitive` or `clinical` permissions is audited and notifies all Owners. Whether it also requires a second approver is an open decision (see roadmap).
- The last active Owner cannot be removed or demoted.
- System role templates cannot be edited by tenants; tenants copy them into custom roles.

## 7. Platform roles and support access

- Platform staff are ordinary users with a `PlatformStaff` record (`super_admin` or `support_admin`). **Platform staff have no organisation access by default.**
- Platform endpoints live under `/api/v1/platform/…`, require MFA, and use a separate permission set (`platform.*`).
- To look inside an organisation, a support admin requests a **SupportAccessGrant**: scope, reason, duration (max 24 h). An organisation Owner approves it. A break-glass path (super admin only, for incidents) skips approval but records the reason, notifies Owners immediately and is reviewed afterwards.
- While a grant is active, the support user gets a synthetic, read-only context limited to the grant's scope. `clinical` data is never in scope for support grants.
- Everything done under a grant is audited with `actor_type=platform_staff` and the grant ID.
