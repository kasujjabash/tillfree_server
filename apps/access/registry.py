"""Collects permission and role-template declarations from every installed app.

Permissions live in code, next to the feature that checks them, so a permission can
never be granted that no code understands. Each app may define in ``permissions.py``:

    PERMISSIONS = [PermissionDef(...), ...]
    ROLE_TEMPLATES = [RoleTemplateDef(...), ...]

A permission's first segment must be the declaring app's label, so a module can only
declare permissions for itself.
"""

from collections.abc import Mapping
from importlib import import_module
from types import MappingProxyType

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

from common.permissions import PermissionDef, RoleTemplateDef, Sensitivity

_permissions: dict[str, PermissionDef] = {}
_templates: dict[str, RoleTemplateDef] = {}

OWNER = "owner"
ADMINISTRATOR = "administrator"
BUILTIN_TEMPLATE_KEYS = frozenset({OWNER, ADMINISTRATOR})


def load() -> None:
    """Called once from AccessConfig.ready()."""
    _permissions.clear()
    _templates.clear()
    for config in apps.get_app_configs():
        try:
            module = import_module(f"{config.name}.permissions")
        except ModuleNotFoundError as exc:
            if exc.name == f"{config.name}.permissions":
                continue
            raise
        for permission in getattr(module, "PERMISSIONS", []):
            if permission.module != config.label:
                raise ImproperlyConfigured(
                    f"{config.label} declares {permission.code}, which belongs to another app."
                )
            if permission.code in _permissions:
                raise ImproperlyConfigured(f"Permission {permission.code} is declared twice.")
            _permissions[permission.code] = permission
        for template in getattr(module, "ROLE_TEMPLATES", []):
            if template.key in _templates or template.key in BUILTIN_TEMPLATE_KEYS:
                raise ImproperlyConfigured(f"Role template {template.key} is declared twice.")
            _templates[template.key] = template
    for template in _templates.values():
        unknown = set(template.permissions) - _permissions.keys()
        if unknown:
            raise ImproperlyConfigured(
                f"Role template {template.key} uses unknown permissions: {sorted(unknown)}"
            )


def permissions() -> Mapping[str, PermissionDef]:
    return MappingProxyType(_permissions)


def get_permission(code: str) -> PermissionDef:
    try:
        return _permissions[code]
    except KeyError:
        raise ImproperlyConfigured(
            f"Unknown permission {code!r}; declare it in permissions.py"
        ) from None


def is_known(code: str) -> bool:
    return code in _permissions


def template_keys() -> list[str]:
    return [OWNER, ADMINISTRATOR, *_templates]


def template_name(key: str) -> str:
    if key == OWNER:
        return "Owner"
    if key == ADMINISTRATOR:
        return "Administrator"
    return _templates[key].name


def template_description(key: str) -> str:
    if key == OWNER:
        return "Full control of the organisation, except clinical records."
    if key == ADMINISTRATOR:
        return "Manages the organisation day to day. No sensitive or clinical data."
    return _templates[key].description


def template_permissions(key: str) -> frozenset[str]:
    """Owner: everything except clinical. Administrator: every standard permission.
    Other templates: exactly what they declare."""
    if key == OWNER:
        return frozenset(
            c for c, p in _permissions.items() if p.sensitivity != Sensitivity.CLINICAL
        )
    if key == ADMINISTRATOR:
        return frozenset(
            c for c, p in _permissions.items() if p.sensitivity == Sensitivity.STANDARD
        )
    template = _templates.get(key)
    return frozenset(template.permissions) if template else frozenset()


def is_template(key: str) -> bool:
    return key in BUILTIN_TEMPLATE_KEYS or key in _templates


def read_only_permissions() -> frozenset[str]:
    """What support staff may use under an approved support-access grant."""
    return frozenset(
        c
        for c, p in _permissions.items()
        if c.endswith(".view") and p.sensitivity == Sensitivity.STANDARD
    )
