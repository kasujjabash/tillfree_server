from common.permissions import PermissionDef, Sensitivity

PERMISSIONS = [
    PermissionDef("organisations.organisation.update", "Edit the organisation profile"),
    PermissionDef(
        "organisations.settings.manage",
        "Change organisation settings, including security policy",
        Sensitivity.SENSITIVE,
    ),
    PermissionDef("organisations.branch.view", "See branches"),
    PermissionDef("organisations.branch.manage", "Create and edit branches"),
    PermissionDef("organisations.department.view", "See departments"),
    PermissionDef("organisations.department.manage", "Create, edit and delete departments"),
]
