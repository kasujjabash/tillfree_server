"""Deployment checks for tenant isolation.

Run against the production database (after migrating) with:

    python manage.py check --deploy --database default
"""

from collections.abc import Sequence
from typing import Any

from django.apps.config import AppConfig
from django.core.checks import CheckMessage, Error, Tags, register
from django.db import connections

from .inspection import rls_status, role_can_bypass_rls, tenant_models


@register(Tags.database, deploy=True)
def check_database_role(
    app_configs: Sequence[AppConfig] | None = None,
    databases: Sequence[str] | None = None,
    **kwargs: Any,
) -> list[CheckMessage]:
    errors: list[CheckMessage] = []
    for alias in databases or []:
        connection = connections[alias]
        if connection.vendor == "postgresql" and role_can_bypass_rls(connection):
            errors.append(
                Error(
                    f"Database '{alias}' connects as a superuser or BYPASSRLS role, so "
                    "row-level security does not apply.",
                    hint="Connect the application as tilltree_app (see infrastructure/db).",
                    id="tenancy.E001",
                )
            )
    return errors


@register(Tags.database, deploy=True)
def check_tenant_tables_have_rls(
    app_configs: Sequence[AppConfig] | None = None,
    databases: Sequence[str] | None = None,
    **kwargs: Any,
) -> list[CheckMessage]:
    errors: list[CheckMessage] = []
    for alias in databases or []:
        connection = connections[alias]
        if connection.vendor != "postgresql":
            continue
        for model in tenant_models():
            status = rls_status(connection, model._meta.db_table)
            if status is None or not status.ok:
                errors.append(
                    Error(
                        f"{model._meta.label} ({model._meta.db_table}) is tenant-owned but "
                        "row-level security is not enabled, forced and policed.",
                        hint="Add EnableTenantRLS to the model's migration.",
                        obj=model,
                        id="tenancy.E002",
                    )
                )
    return errors
