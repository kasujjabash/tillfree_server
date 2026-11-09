"""The access domain's interface for other domains.

API modules of other domains use the tenant view base classes and the authorisation
functions; services use ``AccessContext`` as the actor description.
"""

from .api.base import AnyBranch, TenantAPIMixin, TenantAPIView
from .authorization import (
    AccessContext,
    authorize,
    authorize_anywhere,
    holds,
    permitted_branch_ids,
)

__all__ = [
    "AccessContext",
    "AnyBranch",
    "TenantAPIMixin",
    "TenantAPIView",
    "authorize",
    "authorize_anywhere",
    "holds",
    "permitted_branch_ids",
]
