"""Establishing the active organisation (tenant) and acting user for a unit of work.

Design: docs/architecture/03-multi-tenancy.md §4.

``tenant_context(org_id)`` does two things together:

1. Sets a context variable read by ``TenantManager`` (application-level scoping).
2. Opens a transaction and runs ``set_config('app.organisation_id', <id>, true)``,
   which the PostgreSQL RLS policies read (database-level scoping).

``user_context(user_id)`` sets ``app.user_id`` the same way. It is used by bridge
tables (memberships) whose policy also lets users see their own rows, e.g. to list
the organisations they belong to before choosing one.

The database settings are *transaction-local*: they disappear at COMMIT/ROLLBACK, so
a pooled connection can never carry one request's context into the next. When a
context is entered inside an outer transaction, its setting is explicitly cleared on
exit so it cannot outlive the block.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

import structlog
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.backends.base.base import BaseDatabaseWrapper

from .exceptions import TenancyError, TenantContextConflict

DB_SETTING = "app.organisation_id"
DB_USER_SETTING = "app.user_id"

_current_organisation_id: ContextVar[UUID | None] = ContextVar(
    "current_organisation_id", default=None
)
_current_user_id: ContextVar[UUID | None] = ContextVar("current_user_id", default=None)


def get_current_organisation_id() -> UUID | None:
    return _current_organisation_id.get()


def get_current_user_id() -> UUID | None:
    return _current_user_id.get()


def _set_db_setting(connection: BaseDatabaseWrapper, name: str, value: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [name, value])


def _clear_if_nested(connection: BaseDatabaseWrapper, name: str) -> None:
    if connection.in_atomic_block and not connection.needs_rollback:
        _set_db_setting(connection, name, "")


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
            _set_db_setting(connection, DB_SETTING, str(organisation_id))
            yield
    finally:
        _current_organisation_id.reset(token)
        structlog.contextvars.unbind_contextvars("organisation_id")
        _clear_if_nested(connection, DB_SETTING)


@contextmanager
def user_context(user_id: UUID, *, using: str = DEFAULT_DB_ALIAS) -> Iterator[None]:
    """Mark ``user_id`` as the acting user for row-level policies.

    Must run inside an open transaction (e.g. inside ``tenant_context`` or
    ``transaction.atomic``), because the setting is transaction-local.
    """
    if not isinstance(user_id, UUID):
        raise TypeError("user_id must be a UUID")
    connection = connections[using]
    if not connection.in_atomic_block:
        raise TenancyError("user_context() must be used inside a transaction.")

    current = _current_user_id.get()
    if current is not None:
        if current != user_id:
            raise TenantContextConflict("Cannot switch user inside an active user context.")
        yield
        return

    token = _current_user_id.set(user_id)
    _set_db_setting(connection, DB_USER_SETTING, str(user_id))
    try:
        yield
    finally:
        _current_user_id.reset(token)
        _clear_if_nested(connection, DB_USER_SETTING)
