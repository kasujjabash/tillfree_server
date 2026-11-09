# ADR-0006: Rebuild the foundation; carry forward the inventory and sales logic

- Status: Accepted (2026-11-09)
- Date: 2026-11-09

## Context
The repository contains a pharmacy POS MVP: `accounts` (username-based `AbstractUser`), `businesses` (tenant), `catalog`, `inventory`, `sales`, `reports`, `storefront`, with integer keys, fixed roles, a Kampala server timezone and pharmacy columns on the shared product model. Changing the user model and key types after real data exists requires complex data migrations across every table.

## Decision
- Phase 1 starts a fresh migration history with the new `identity.User` and UUIDv7 keys.
- The MVP apps are deleted from the tree. They stay available in git history at commit `69fdfdb` as the reference for the port.
- In Phase 4 the inventory logic (FEFO batch allocation with row locks, stock movement ledger, write-offs, per-line cost capture) is ported into `apps/shared/inventory` and the sales logic into the business module, rewritten against the new tenancy, permissions and money conventions, with their existing tests adapted.

## Consequences
- No migration of existing data (confirmed by the product owner: the code may be rewritten).
- The working demo stops working until Phases 4–5 restore equivalent features. If the MVP is needed for pilots in the meantime, it should be deployed from the current commit, not maintained in parallel.

## Alternatives
- Evolve the MVP in place: every foundational change (user model, keys, tenancy, roles) becomes a data migration with risk and little benefit. Rejected unless production data exists.
