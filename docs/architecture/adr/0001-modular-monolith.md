# ADR-0001: Modular monolith

- Status: Accepted (2026-11-09)
- Date: 2026-11-09

## Context
The platform has many domains (identity, organisations, inventory, healthcare…) but no measured scaling need and a small team. Microservices bring network failures, distributed transactions, separate deployments and observability overhead.

## Decision
Build one Django codebase, deployed as one image, run as API, worker and beat processes. Domains are Django apps with enforced boundaries: a domain is used by others only through its `public.py`, writes happen only through its own services, dependencies point one way, and peers talk through outbox events. import-linter enforces these rules in CI.

## Consequences
- Simple transactions across a request; one deploy; easy local setup.
- Boundary discipline is required; CI enforcement makes it cheap.
- A domain can be extracted later by replacing its `public.py` with an API client and its events with a message bus. Foreign keys to foundation entities (Organisation, User, Branch) are the main coupling to cut at that point.

## Alternatives
- Microservices from the start: rejected (cost without need).
- Unstructured monolith: rejected (impossible to extract later, cross-domain writes everywhere).
