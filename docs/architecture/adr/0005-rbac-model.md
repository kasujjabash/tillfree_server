# ADR-0005: Code-declared permissions, per-organisation roles, branch-scoped assignments

- Status: Accepted (2026-11-09)
- Date: 2026-11-09

## Context
Organisations need roles that fit their structure; industry modules bring their own roles; some data (clinical, payroll, cost prices) must not be visible to administrators by default; branch-level restriction is required.

## Decision
- Permissions are strings `module.resource.action`, declared in each module's code with a sensitivity class (`standard`, `sensitive`, `clinical`) and synced to a table at deploy time.
- Roles are data owned by an organisation; the platform and modules ship read-only templates that organisations can copy.
- A role assignment is `(membership, role, branch | null)`.
- One `authorize()` function decides, combining permission, module entitlement, branch scope and optional object policies. Views declare required permissions per action; a test fails if a view action has none.
- Owner/Admin templates exclude clinical permissions; grants are limited to permissions the granter holds.

## Consequences
- Adding a feature means declaring its permissions next to its code, which keeps checks reviewable.
- Effective permissions are cached per membership with version-based invalidation.
- Object-level policies (e.g. care-team access) plug in per domain without changing the core.

## Alternatives
- Django's built-in `Group`/`Permission`: global, not organisation-scoped, model-CRUD oriented. Rejected.
- Fixed role enum per membership (current MVP): cannot express custom or multi-branch roles. Rejected.
- Full ABAC policy engine (e.g. OPA): more power than needed now, extra infrastructure. Rejected; object policies cover the attribute cases we know of.
