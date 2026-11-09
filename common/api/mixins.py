from contextlib import ExitStack
from typing import Any, cast
from uuid import UUID

from django.db import transaction
from django.http import HttpRequest, HttpResponseBase
from rest_framework.request import Request

from common.tenancy.context import user_context


class UserScopedMixin:
    """For endpoints about the signed-in user across organisations (e.g. "my memberships").

    Runs the request in a transaction with the user's id set for row-level policies, so
    the user can read their own rows of bridge tables without any organisation context.
    """

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        with ExitStack() as stack:
            stack.enter_context(transaction.atomic())
            self._context_stack = stack
            return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)  # type: ignore[misc]
        self._context_stack.enter_context(user_context(cast(UUID, request.user.pk)))
