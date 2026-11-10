"""Object storage behind a small interface: S3-compatible in production, memory in tests.

Select with ``STORAGE_BACKEND``. Buckets are private; clients only ever receive
short-lived presigned URLs for one exact key.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from typing import Any, ClassVar, Protocol

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    fields: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    content_type: str


class ObjectStorage(Protocol):
    def presign_upload(
        self, key: str, *, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedUpload: ...
    def presign_download(
        self, key: str, *, filename: str, content_type: str, expires_in: int
    ) -> str: ...
    def head(self, key: str) -> ObjectInfo | None: ...
    def stream(self, key: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]: ...
    def delete(self, key: str) -> None: ...


def _content_disposition(filename: str) -> str:
    safe = "".join(ch for ch in filename if ch.isalnum() or ch in " ._-")[:100] or "file"
    return f'attachment; filename="{safe}"'


class S3ObjectStorage:
    """Any S3-compatible service (AWS S3, MinIO, Cloudflare R2, Wasabi, ...)."""

    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        options: dict[str, Any] = {
            "region_name": settings.S3_REGION,
            "aws_access_key_id": settings.S3_ACCESS_KEY_ID or None,
            "aws_secret_access_key": settings.S3_SECRET_ACCESS_KEY or None,
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3},
            ),
        }
        self.bucket = settings.S3_BUCKET
        self.client = boto3.client("s3", endpoint_url=settings.S3_ENDPOINT_URL or None, **options)
        # Presigned URLs must use the address browsers can reach (e.g. localhost vs. minio).
        self.signer = boto3.client(
            "s3",
            endpoint_url=settings.S3_PUBLIC_ENDPOINT_URL or settings.S3_ENDPOINT_URL or None,
            **options,
        )

    def presign_upload(
        self, key: str, *, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedUpload:
        post = self.signer.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, max_bytes]],
            ExpiresIn=expires_in,
        )
        return PresignedUpload(
            url=post["url"],
            fields=dict(post["fields"]),
            expires_at=timezone.now() + timedelta(seconds=expires_in),
        )

    def presign_download(
        self, key: str, *, filename: str, content_type: str, expires_in: int
    ) -> str:
        url: str = self.signer.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": _content_disposition(filename),
                "ResponseContentType": content_type,
            },
            ExpiresIn=expires_in,
        )
        return url

    def head(self, key: str) -> ObjectInfo | None:
        from botocore.exceptions import ClientError

        try:
            info = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return ObjectInfo(
            size=int(info["ContentLength"]), content_type=str(info.get("ContentType", ""))
        )

    def stream(self, key: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        yield from body.iter_chunks(chunk_size)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


class InMemoryObjectStorage:
    """Tests and local runs without object storage. Shared per process."""

    objects: ClassVar[dict[str, tuple[bytes, str]]] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    def presign_upload(
        self, key: str, *, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedUpload:
        return PresignedUpload(
            url="memory://upload",
            fields={"key": key, "Content-Type": content_type},
            expires_at=timezone.now() + timedelta(seconds=expires_in),
        )

    def presign_download(
        self, key: str, *, filename: str, content_type: str, expires_in: int
    ) -> str:
        return f"memory://{key}?expires_in={expires_in}"

    def head(self, key: str) -> ObjectInfo | None:
        if key not in self.objects:
            return None
        data, content_type = self.objects[key]
        return ObjectInfo(size=len(data), content_type=content_type)

    def stream(self, key: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        data = self.objects[key][0]
        for start in range(0, len(data), chunk_size):
            yield data[start : start + chunk_size]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@cache
def _storage(path: str) -> ObjectStorage:
    instance: ObjectStorage = import_string(path)()
    return instance


def storage() -> ObjectStorage:
    return _storage(settings.STORAGE_BACKEND)
