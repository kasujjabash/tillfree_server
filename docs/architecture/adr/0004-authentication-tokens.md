# ADR-0004: Short-lived JWT access tokens with server-side rotating refresh sessions

- Status: Accepted (2026-11-09)
- Date: 2026-11-09

## Context
Clients will include web and mobile apps. We need stateless request authentication for horizontal scaling, plus immediate logout, per-device session management and protection against stolen refresh tokens.

## Decision
- Access token: JWT (10 min) with `sub`, `sid`, `jti`, `iat`, `exp`, `auth_time`, `amr`. No organisation, role or personal data.
- Refresh token: opaque random value, stored hashed in an `AuthSession` row; rotated on every use; reuse of an old token revokes the token family.
- Revoked session IDs kept in Redis for the access-token lifetime and checked on each request.
- Web clients receive the refresh token in an `HttpOnly; Secure; SameSite=Strict` cookie scoped to the auth path; mobile clients receive it in the body.
- Organisation context comes from the `X-Organisation-ID` header on each request, validated against membership.

## Consequences
- Logout and revocation take effect on the next request.
- A small custom auth module instead of relying on `djangorestframework-simplejwt`'s blacklist; PyJWT used for signing with key IDs for rotation.
- Role changes take effect immediately because permissions are not embedded in tokens.

## Alternatives
- Long-lived JWTs (the current MVP uses 8 h): cannot be revoked. Rejected.
- Server sessions only (cookies): poor fit for mobile and cross-origin clients. Kept only for Django admin.
- Organisation embedded in the token: forces re-login to switch organisation, and tokens go stale when membership changes. Rejected.
