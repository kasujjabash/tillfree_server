"""Uploaded files: metadata here, bytes in private object storage (never in PostgreSQL).

Storage keys are ``org/<organisation id>/files/<file id>``: the organisation prefix keeps
tenants apart in the bucket, and the original filename never appears in the key.
"""

from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.tenancy.models import TenantModel


class FileObject(TenantModel):
    class Status(models.TextChoices):
        PENDING_UPLOAD = "pending_upload", _("Waiting for upload")
        PROCESSING = "processing", _("Being checked")
        AVAILABLE = "available", _("Available")
        REJECTED = "rejected", _("Rejected")

    class Classification(models.TextChoices):
        STANDARD = "standard", _("Standard")
        SENSITIVE = "sensitive", _("Sensitive")

    storage_key = models.CharField(max_length=300, unique=True, editable=False)
    original_name = models.CharField(max_length=255)
    declared_content_type = models.CharField(max_length=100)
    content_type = models.CharField(
        max_length=100, blank=True, help_text=_("Detected from the content.")
    )
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_UPLOAD)
    rejection_reason = models.CharField(max_length=50, blank=True)
    classification = models.CharField(
        max_length=20, choices=Classification.choices, default=Classification.STANDARD
    )
    purpose = models.CharField(max_length=50, blank=True, help_text=_("e.g. organisations.logo"))
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    uploaded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "documents_file"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(size_bytes__gt=0), name="documents_size_positive"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["organisation", "status"], name="documents_status_idx"),
        ]

    def __str__(self) -> str:
        return self.original_name
