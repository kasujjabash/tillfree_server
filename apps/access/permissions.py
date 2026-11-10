from common.permissions import PermissionDef, RoleTemplateDef, Sensitivity

PERMISSIONS = [
    PermissionDef("access.membership.view", "See the organisation's members"),
    PermissionDef("access.membership.manage", "Suspend, reactivate and remove members"),
    PermissionDef("access.invitation.manage", "Invite people to the organisation"),
    PermissionDef("access.role.view", "See roles and who holds them"),
    PermissionDef("access.role.manage", "Create, edit and delete custom roles"),
    PermissionDef("access.role.assign", "Give roles to members and take them away"),
    PermissionDef(
        "access.support.manage",
        "Approve, deny and revoke platform support access to this organisation",
        Sensitivity.SENSITIVE,
    ),
]

ROLE_TEMPLATES = [
    RoleTemplateDef(
        key="manager",
        name="Manager",
        description="Sees the organisation's structure and team.",
        permissions=(
            "organisations.branch.view",
            "organisations.department.view",
            "access.membership.view",
            "access.role.view",
            "documents.file.upload",
            "documents.file.view",
            "subscriptions.subscription.view",
        ),
    ),
    RoleTemplateDef(
        key="staff",
        name="Staff member",
        description="Basic access for team members.",
        permissions=(
            "organisations.branch.view",
            "organisations.department.view",
            "documents.file.upload",
        ),
    ),
]
