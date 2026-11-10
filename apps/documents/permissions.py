from common.permissions import PermissionDef, Sensitivity

PERMISSIONS = [
    PermissionDef("documents.file.upload", "Upload files"),
    PermissionDef("documents.file.view", "See and download the organisation's files"),
    PermissionDef(
        "documents.file.view_sensitive",
        "See and download files marked sensitive",
        Sensitivity.SENSITIVE,
    ),
    PermissionDef("documents.file.delete", "Delete any of the organisation's files"),
]
