"""Establishing the active organisation (tenant) for a unit of work.

Design: docs/architecture/03-multi-tenancy.md §4.

``tenant_context(org_id)`` does two things together:

1. Sets a context variable read by ``TenantManager`` (application-level scoping).
2. Opens a transaction and runs ``set_config('app.organisation_id', <id>, true)``,
   which the PostgreSQL RLS policies read (database-level scoping).

The database setting is *transaction-local*: it disappears at COMMIT/ROLLBACK, so a
pooled connection can never carry one request's tenant into the next. When the
context is entered inside an outer transaction, the setting is explicitly cleared
on exit so it cannot outlive the block.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

import structlog
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.backends.base.base import BaseDatabaseWrapper

from .exceptions import TenantContextConflict

DB_SETTING = "app.organisation_id"

_current_organisation_id: ContextVar[UUID | None] = ContextVar(
    "current_organisation_id", default=None
)


def get_current_organisation_id() -> UUID | None:
    return _current_organisation_id.get()


def _set_db_setting(connection: BaseDatabaseWrapper, value: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [DB_SETTING, value])


@contextmanager
def tenant_context(organisation_id: UUID, *, using: str = DEFAULT_DB_ALIAS) -> Iterator[None]:
    """Run the enclosed block as ``organisation_id``, inside a database transaction.

    Re-entering with the same organisation is allowed; switching to a different
    organisation inside an active context raises ``TenantContextConflict``.
    """
    if not isinstance(organisation_id, UUID):
        raise TypeError("organisation_id must be a UUID")

    current = _current_organisation_id.get()
    if current is not None:
        if current != organisation_id:
            raise TenantContextConflict(
                "Cannot switch organisation inside an active tenant context."
            )
        yield
        return

    connection = connections[using]
    token = _current_organisation_id.set(organisation_id)
    structlog.contextvars.bind_contextvars(organisation_id=str(organisation_id))
    try:
        with transaction.atomic(using=using):
            _set_db_setting(connection, str(organisation_id))
            yield
    finally:
        _current_organisation_id.reset(token)
        structlog.contextvars.unbind_contextvars("organisation_id")
        # Our atomic block was a savepoint inside an outer transaction: clear the
        # setting so it does not leak into the rest of that transaction.
        if connection.in_atomic_block and not connection.needs_rollback:
            _set_db_setting(connection, "")
