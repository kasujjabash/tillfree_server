"""Files: start an upload, complete it, list, download (short-lived link) and delete.

Who may see a file: whoever uploaded it, or holders of ``documents.file.view``
(plus ``documents.file.view_sensitive`` for sensitive files).
"""

from typing import Any, ClassVar

from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.access.public import AccessContext, TenantAPIMixin, holds
from apps.identity.public import User
from apps.subscriptions.public import ensure_within_limit
from common.api.pagination import SmallListPagination
from common.exceptions import NotFoundError, PermissionDeniedError

from .. import services
from ..models import FileObject

UUID_LOOKUP = r"[0-9a-f-]{36}"


def _user(request: Request) -> User:
    if not isinstance(request.user, User):
        raise NotAuthenticated()
    return request.user


class FileSerializer(serializers.ModelSerializer[FileObject]):
    class Meta:
        model = FileObject
        fields = (
            "id",
            "original_name",
            "content_type",
            "size_bytes",
            "sha256",
            "status",
            "rejection_reason",
            "classification",
            "purpose",
            "uploaded_at",
            "created_at",
        )


class StartUploadSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=255)
    content_type = serializers.CharField(max_length=100)
    size_bytes = serializers.IntegerField(min_value=1)
    classification = serializers.ChoiceField(
        choices=FileObject.Classification.choices, default="standard"
    )
    purpose = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")


class UploadInstructionsSerializer(serializers.Serializer[Any]):
    url = serializers.CharField()
    form_fields = serializers.DictField(
        child=serializers.CharField(), source="fields", help_text="Send these with the file."
    )
    expires_at = serializers.DateTimeField()


class StartUploadResponseSerializer(serializers.Serializer[Any]):
    file = FileSerializer()
    upload = UploadInstructionsSerializer()


class DownloadSerializer(serializers.Serializer[Any]):
    url = serializers.CharField()
    expires_at = serializers.DateTimeField()


def can_view(ctx: AccessContext, file: FileObject) -> bool:
    if file.uploaded_by_id == ctx.user_id:
        return True
    if not holds(ctx, "documents.file.view"):
        return False
    return file.classification != FileObject.Classification.SENSITIVE or holds(
        ctx, "documents.file.view_sensitive"
    )


class FileViewSet(
    TenantAPIMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet[FileObject],
):
    queryset = FileObject.objects.none()  # schema introspection; get_queryset() is used
    serializer_class = FileSerializer
    pagination_class = SmallListPagination
    lookup_value_regex = UUID_LOOKUP
    filter_backends = ()
    required_permissions: ClassVar[dict[str, Any]] = {
        "list": "documents.file.view",
        "create": "documents.file.upload",
        # Object-level checks below (uploader, or view permissions for the classification).
        "retrieve": None,
        "complete": None,
        "download": None,
        "destroy": None,
    }

    def get_queryset(self) -> QuerySet[FileObject]:
        return services.list_files(
            include_sensitive=holds(self.ctx, "documents.file.view_sensitive")
        )

    def _visible_file(self, pk: str) -> FileObject:
        file = services.get_file(pk)
        if file is None or not can_view(self.ctx, file):
            raise NotFoundError("File not found.")  # never confirm a file exists
        return file

    @extend_schema(request=StartUploadSerializer, responses={201: StartUploadResponseSerializer})
    def create(self, request: Request) -> Response:
        data = StartUploadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        if v["classification"] == FileObject.Classification.SENSITIVE and not holds(
            self.ctx, "documents.file.view_sensitive"
        ):
            raise PermissionDeniedError(
                "You cannot upload sensitive files.", code="permission_denied"
            )
        ensure_within_limit(
            self.ctx.entitlements,
            "storage_bytes",
            current=services.storage_used_bytes(),
            adding=v["size_bytes"],
        )
        file, upload = services.start_upload(
            organisation_id=self.ctx.organisation_id, user=_user(request), **v
        )
        body = {"file": file, "upload": upload.__dict__}
        return Response(StartUploadResponseSerializer(body).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={200: FileSerializer})
    def retrieve(self, request: Request, pk: str) -> Response:
        return Response(FileSerializer(self._visible_file(pk)).data)

    @extend_schema(request=None, responses={200: FileSerializer})
    @action(detail=True, methods=["post"])
    def complete(self, request: Request, pk: str) -> Response:
        file = self._visible_file(pk)
        if file.uploaded_by_id != self.ctx.user_id:
            raise NotFoundError("File not found.")
        return Response(FileSerializer(services.complete_upload(file)).data)

    @extend_schema(responses={200: DownloadSerializer})
    @action(detail=True, methods=["get"])
    def download(self, request: Request, pk: str) -> Response:
        if self.ctx.is_support:
            # Support access may see that files exist, never their contents.
            raise PermissionDeniedError(
                "Support access cannot download files.", code="support_read_only"
            )
        url, expires_at = services.download_url(self._visible_file(pk), user=_user(request))
        response = Response(DownloadSerializer({"url": url, "expires_at": expires_at}).data)
        response["Cache-Control"] = "no-store"
        return response

    @extend_schema(responses={204: None})
    def destroy(self, request: Request, pk: str) -> Response:
        file = self._visible_file(pk)
        if file.uploaded_by_id != self.ctx.user_id and not holds(self.ctx, "documents.file.delete"):
            raise PermissionDeniedError("You cannot delete this file.")
        services.delete_file(file, user=_user(request))
        return Response(status=status.HTTP_204_NO_CONTENT)
