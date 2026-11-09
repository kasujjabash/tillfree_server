"""OpenAPI generation that documents tenant requirements on every tenant endpoint."""

from typing import Any

from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiParameter

from .base import ORGANISATION_HEADER, AnyBranch, TenantAPIMixin


class TenantAwareAutoSchema(AutoSchema):
    def _is_tenant(self) -> bool:
        return isinstance(self.view, TenantAPIMixin)

    def get_override_parameters(self) -> list[Any]:
        parameters = list(super().get_override_parameters())
        if self._is_tenant():
            parameters.append(
                OpenApiParameter(
                    ORGANISATION_HEADER,
                    type=str,
                    location=OpenApiParameter.HEADER,
                    required=True,
                    description="ID of the organisation to act in. Non-members get 404.",
                )
            )
        return parameters

    def get_description(self) -> str:  # type: ignore[override]  # drf-spectacular signature
        description = super().get_description() or ""
        if not self._is_tenant():
            return description
        view = self.view
        key = getattr(view, "action", None) or self.method.lower()
        requirement = getattr(view, "required_permissions", {}).get(key)
        if requirement is None:
            note = "Any active member of the organisation."
        elif isinstance(requirement, AnyBranch):
            note = (
                f"Requires `{requirement.code}` in at least one branch "
                "(results limited to those branches)."
            )
        else:
            note = f"Requires `{requirement}` for the whole organisation."
        return f"{description}\n\n**Permission:** {note}".strip()
