"""Upload, verification, download and deletion of files (docs/architecture/07-security.md §2).

1. ``start_upload``: check type/size, record the file, return a presigned POST for one key.
2. The client uploads directly to object storage.
3. ``complete_upload``: confirm the object exists with the declared size; verify in the
   background (real type from the first bytes, SHA-256, malware scan).
4. ``download_url``: only for available files, a link valid for 60 seconds.
"""

import hashlib
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from uuid import UUID

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.public import record
from apps.identity.public import User
from common.exceptions import BusinessRuleViolation, ConflictError, NotFoundError

from .inspection import scanner, sniff
from .models import FileObject
from .storage import PresignedUpload, storage

logger = structlog.get_logger("tillfree.documents")

UPLOAD_URL_SECONDS = 300
DOWNLOAD_URL_SECONDS = 60
SNIFF_BYTES = 16


def storage_used_bytes() -> int:
    """Bytes held by the organisation in context (everything not rejected)."""
    total = FileObject.objects.exclude(status=FileObject.Status.REJECTED).aggregate(
        total=Sum("size_bytes")
    )["total"]
    return int(total or 0)


def list_files(*, include_sensitive: bool) -> QuerySet[FileObject]:
    files = FileObject.objects.select_related("uploaded_by").order_by("-created_at")
    return (
        files
        if include_sensitive
        else files.exclude(classification=FileObject.Classification.SENSITIVE)
    )


def get_file(file_id: UUID | str) -> FileObject | None:
    return FileObject.objects.select_related("uploaded_by").filter(pk=file_id).first()


@transaction.atomic
def start_upload(
    *,
    organisation_id: UUID,
    user: User,
    name: str,
    content_type: str,
    size_bytes: int,
    classification: str,
    purpose: str = "",
) -> tuple[FileObject, PresignedUpload]:
    if content_type not in settings.FILES_ALLOWED_TYPES:
        raise BusinessRuleViolation(
            _("This type of file is not accepted."),
            code="file_type_not_allowed",
            field="content_type",
        )
    if not 0 < size_bytes <= settings.FILES_MAX_BYTES:
        raise BusinessRuleViolation(
            _("The file is too large."), code="file_too_large", field="size_bytes"
        )
    file = FileObject(
        original_name=name[:255],
        declared_content_type=content_type,
        size_bytes=size_bytes,
        classification=classification,
        purpose=purpose,
        uploaded_by=user,
    )
    file.storage_key = f"org/{organisation_id}/files/{file.id}"
    file.save()
    upload = storage().presign_upload(
        file.storage_key,
        content_type=content_type,
        max_bytes=size_bytes,
        expires_in=UPLOAD_URL_SECONDS,
    )
    record(
        "file.upload_started",
        organisation_id=organisation_id,
        actor_id=user.id,
        resource_type="file",
        resource_id=file.id,
        metadata={
            "content_type": content_type,
            "size_bytes": size_bytes,
            "classification": classification,
        },
    )
    return file, upload


@transaction.atomic
def complete_upload(file: FileObject) -> FileObject:
    if file.status != FileObject.Status.PENDING_UPLOAD:
        raise ConflictError(_("This upload was already completed."), code="already_completed")
    info = storage().head(file.storage_key)
    if info is None:
        raise BusinessRuleViolation(
            _("The file has not been uploaded yet."), code="upload_not_found"
        )
    if info.size != file.size_bytes:
        _reject(file, "size_mismatch")
        return file
    file.status = FileObject.Status.PROCESSING
    file.uploaded_at = timezone.now()
    file.save(update_fields=["status", "uploaded_at", "updated_at"])
    _queue_verification(file)
    return file


def _queue_verification(file: FileObject) -> None:
    from .tasks import verify_file

    file_id, organisation_id = str(file.id), str(file.organisation_id)
    transaction.on_commit(
        lambda: verify_file.delay(file_id=file_id, organisation_id=organisation_id), robust=True
    )


def _reject(file: FileObject, reason: str) -> None:
    file.status = FileObject.Status.REJECTED
    file.rejection_reason = reason
    file.save(update_fields=["status", "rejection_reason", "updated_at"])
    key = file.storage_key
    transaction.on_commit(lambda: storage().delete(key), robust=True)
    record(
        "file.rejected",
        organisation_id=file.organisation_id,
        resource_type="file",
        resource_id=file.id,
        metadata={"reason": reason},
    )


def verify(file: FileObject) -> FileObject:
    """Background check: real type, hash and malware scan. Runs in the tenant context."""
    if file.status != FileObject.Status.PROCESSING:
        return file
    chunks = storage().stream(file.storage_key)
    first = next(chunks, b"")
    detected = sniff(first[:SNIFF_BYTES])
    if detected is None or detected != file.declared_content_type:
        _reject(file, "content_type_mismatch")
        return file
    digest = hashlib.sha256()

    def hashed() -> Iterator[bytes]:
        for chunk in (first, *chunks):
            digest.update(chunk)
            yield chunk

    result = scanner().scan(hashed())
    if not result.clean:
        logger.warning("file_malware_detected", signature=result.signature)
        _reject(file, "malware_detected")
        return file
    file.content_type = detected
    file.sha256 = digest.hexdigest()
    file.status = FileObject.Status.AVAILABLE
    file.save(update_fields=["content_type", "sha256", "status", "updated_at"])
    return file


def download_url(file: FileObject, *, user: User) -> tuple[str, Any]:
    if file.status != FileObject.Status.AVAILABLE:
        raise ConflictError(_("This file is not available."), code="file_not_available")
    url = storage().presign_download(
        file.storage_key,
        filename=file.original_name,
        content_type=file.content_type,
        expires_in=DOWNLOAD_URL_SECONDS,
    )
    record(
        "file.downloaded",
        organisation_id=file.organisation_id,
        actor_id=user.id,
        resource_type="file",
        resource_id=file.id,
    )
    return url, timezone.now() + timedelta(seconds=DOWNLOAD_URL_SECONDS)


@transaction.atomic
def delete_file(file: FileObject, *, user: User) -> None:
    file_id, organisation_id, key = file.id, file.organisation_id, file.storage_key
    file.delete()
    transaction.on_commit(lambda: storage().delete(key), robust=True)
    record(
        "file.deleted",
        organisation_id=organisation_id,
        actor_id=user.id,
        resource_type="file",
        resource_id=file_id,
    )


def purge_stale_uploads(*, older_than: timedelta = timedelta(days=1)) -> int:
    """Uploads started but never completed (organisation in context)."""
    stale = list(
        FileObject.objects.filter(
            status=FileObject.Status.PENDING_UPLOAD, created_at__lt=timezone.now() - older_than
        )
    )
    for file in stale:
        key = file.storage_key
        file.delete()
        transaction.on_commit(lambda key=key: storage().delete(key), robust=True)  # type: ignore[misc]
    return len(stale)


def require_file(file_id: UUID | str) -> FileObject:
    file = get_file(file_id)
    if file is None:
        raise NotFoundError(_("File not found."))
    return file
