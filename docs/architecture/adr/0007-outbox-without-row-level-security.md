# ADR-0007: Transactional outbox for domain events, outside row-level security

- Status: Accepted (2026-11-10)

## Context
Domains must react to each other without importing each other ("a member joined" → notify managers), and side effects must never happen for a change that rolled back or be lost when the queue is briefly down. The relay that delivers events reads events of every organisation.

## Decision
- `apps.events.publish()` writes an `OutboxEvent` row in the caller's transaction. After commit, a relay task (`relay_outbox`, also scheduled every 30 s as a backstop) hands each event to its registered handlers as separate Celery tasks.
- Handlers are declared with `@on_event("<type>")` in a domain's `handlers.py`. Each handler runs inside the event's tenant context. A `ProcessedEvent(event_id, handler)` row is written in the same transaction as the handler's effects, so effects happen once even though delivery is at-least-once.
- The outbox and processed-event tables are **not** under row-level security. They are internal (never exposed through the API) and payloads are validated to be identifiers and short facts only (no nested data, strings ≤ 200 chars), so they hold no tenant content. Handlers load the data they need inside the tenant context, where RLS applies.

## Consequences
- No dual-write bugs between the database and the queue; a queue outage delays reactions instead of losing them.
- The relay needs no privileged database role.
- Adding a reaction to an event never touches the publishing domain.

## Alternatives
- Calling other domains' services directly: creates upward dependencies and couples transactions to slow work.
- Publishing to Celery directly in `on_commit`: loses events if the broker is down at that moment.
- RLS on the outbox with a BYPASSRLS relay role: more operational complexity for a table that holds no tenant content.
