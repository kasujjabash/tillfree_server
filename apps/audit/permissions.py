from common.permissions import PermissionDef, Sensitivity

PERMISSIONS = [
    PermissionDef("audit.event.view", "Read the organisation's audit log", Sensitivity.SENSITIVE),
]
