"""Permission declarations.

Each domain app declares the permissions it checks in ``<app>/permissions.py``::

    from common.permissions import PermissionDef, Sensitivity

    PERMISSIONS = [
        PermissionDef("organisations.branch.view", "See branches"),
        PermissionDef("audit.event.view", "Read the audit log", Sensitivity.SENSITIVE),
    ]

The access domain collects them at startup (docs/architecture/05-identity-and-access.md §6).
"""

import re
from dataclasses import dataclass
from enum import StrEnum

_CODE = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*\.[a-z][a-z_]*$")


class Sensitivity(StrEnum):
    STANDARD = "standard"
    SENSITIVE = "sensitive"  # payroll, costs, exports, security settings, audit log
    CLINICAL = "clinical"  # patient data: never in Owner/Administrator templates; needs MFA


@dataclass(frozen=True)
class PermissionDef:
    code: str  # "<module>.<resource>.<action>"
    description: str
    sensitivity: Sensitivity = Sensitivity.STANDARD

    def __post_init__(self) -> None:
        if not _CODE.match(self.code):
            raise ValueError(f"Permission code {self.code!r} must look like module.resource.action")

    @property
    def module(self) -> str:
        return self.code.split(".", 1)[0]


@dataclass(frozen=True)
class RoleTemplateDef:
    """A ready-made role a module offers (e.g. healthcare's "Clinician").

    Declared in ``<app>/permissions.py`` as ``ROLE_TEMPLATES = [...]``. Organisations
    assign templates directly or copy them into custom roles.
    """

    key: str
    name: str
    description: str
    permissions: tuple[str, ...]
