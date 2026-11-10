"""A test-only permission in a module no plan includes (used to test module gating)."""

from common.permissions import PermissionDef

PERMISSIONS = [PermissionDef("tenancy_app.widget.view", "See widgets (test only)")]
