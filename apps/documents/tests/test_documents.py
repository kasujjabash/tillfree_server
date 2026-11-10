import socket
import struct
import threading
from datetime import timedelta
from typing import Any

import pytest
from django.test import override_settings

from apps.access.models import SupportAccessGrant
from apps.documents.inspection import ClamdScanner, ScanResult, sniff
from apps.documents.models import FileObject
from apps.documents.storage import InMemoryObjectStorage
from common.tenancy.context import tenant_context

pytestmark = pytest.mark.django_db

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
PDF = b"%PDF-1.7\n" + b"x" * 100


@pytest.fixture(autouse=True)
def _clean_storage(django_capture_on_commit_callbacks):
    global _capture
    InMemoryObjectStorage.objects.clear()
    _capture = django_capture_on_commit_callbacks


_capture: Any = None


def _upload(
    client,
    *,
    data=PNG,
    content_type="image/png",
    name="logo.png",
    classification="standard",
    complete=True,
):
    started = client.post(
        "/api/v1/files/",
        {
            "name": name,
            "content_type": content_type,
            "size_bytes": len(data),
            "classification": classification,
        },
        format="json",
    )
    assert started.status_code == 201, started.json()
    body = started.json()
    InMemoryObjectStorage().put(body["upload"]["form_fields"]["key"], data, content_type)
    if not complete:
        return body["file"]
    with _capture(execute=True):  # verification runs after commit, as in production
        assert client.post(f"/api/v1/files/{body['file']['id']}/complete/").status_code == 200
    return client.get(f"/api/v1/files/{body['file']['id']}/").json()


def test_upload_flow_verifies_and_allows_download(new_org, client_for):
    org, owner = new_org()
    client = client_for(owner, org)
    file = _upload(client)
    assert file["status"] == "available"
    assert file["content_type"] == "image/png" and len(file["sha256"]) == 64
    download = client.get(f"/api/v1/files/{file['id']}/download/")
    assert download.status_code == 200 and "expires_in=60" in download.json()["url"]
    with tenant_context(org.id):
        assert FileObject.objects.get().storage_key.startswith(f"org/{org.id}/files/")


def test_presigned_upload_is_limited_to_one_key(new_org, client_for):
    org, owner = new_org()
    body = (
        client_for(owner, org)
        .post(
            "/api/v1/files/",
            {"name": "../../etc/passwd", "content_type": "image/png", "size_bytes": 10},
            format="json",
        )
        .json()
    )
    key = body["upload"]["form_fields"]["key"]
    assert key == f"org/{org.id}/files/{body['file']['id']}"  # the filename never reaches the key


@pytest.mark.parametrize(
    ("content_type", "size", "code"),
    [
        ("text/html", 10, "file_type_not_allowed"),
        ("image/svg+xml", 10, "file_type_not_allowed"),
        ("image/png", 50 * 1024 * 1024, "file_too_large"),
    ],
)
def test_dangerous_or_huge_uploads_are_refused(new_org, client_for, content_type, size, code):
    org, owner = new_org()
    response = client_for(owner, org).post(
        "/api/v1/files/",
        {"name": "x", "content_type": content_type, "size_bytes": size},
        format="json",
    )
    assert response.status_code == 400 and response.json()["errors"][0]["code"] == code


def test_disguised_file_is_rejected_and_deleted(new_org, client_for):
    org, owner = new_org()
    file = _upload(client_for(owner, org), data=PDF, content_type="image/png", name="photo.png")
    assert (file["status"], file["rejection_reason"]) == ("rejected", "content_type_mismatch")
    assert InMemoryObjectStorage.objects == {}


def test_malware_is_rejected(new_org, client_for, monkeypatch):
    org, owner = new_org()
    monkeypatch.setattr(
        "apps.documents.inspection.NoScanner.scan",
        lambda self, chunks: ScanResult(clean=False, signature="Eicar"),
    )
    file = _upload(client_for(owner, org))
    assert (file["status"], file["rejection_reason"]) == ("rejected", "malware_detected")
    assert client_for(owner, org).get(f"/api/v1/files/{file['id']}/download/").status_code == 409


def test_completing_before_uploading_is_refused(new_org, client_for):
    org, owner = new_org()
    client = client_for(owner, org)
    file = client.post(
        "/api/v1/files/",
        {"name": "a.png", "content_type": "image/png", "size_bytes": 10},
        format="json",
    ).json()["file"]
    response = client.post(f"/api/v1/files/{file['id']}/complete/")
    assert response.status_code == 400 and response.json()["code"] == "upload_not_found"


def test_storage_quota_is_enforced(new_org, client_for):
    org, owner = new_org()
    with tenant_context(org.id):
        from apps.subscriptions.models import Plan

        Plan.objects.filter(code="starter").update(limits={"storage_bytes": 150})
    client = client_for(owner, org)
    _upload(client)  # 108 bytes
    over = client.post(
        "/api/v1/files/",
        {"name": "b.png", "content_type": "image/png", "size_bytes": 100},
        format="json",
    )
    assert over.status_code == 403 and over.json()["code"] == "plan_limit_reached"


def test_who_can_see_files(new_org, add_member, client_for):
    org, owner = new_org()
    staff, _ = add_member(org, "staff@example.com", "staff")  # may upload, not view others' files
    manager, _ = add_member(org, "m@example.com", "manager")  # may view standard files
    owner_client = client_for(owner, org)
    standard = _upload(owner_client)
    sensitive = _upload(owner_client, classification="sensitive")
    own = _upload(client_for(staff, org))

    staff_client = client_for(staff, org)
    assert staff_client.get(f"/api/v1/files/{standard['id']}/").status_code == 404
    assert staff_client.get(f"/api/v1/files/{own['id']}/").status_code == 200
    assert staff_client.get("/api/v1/files/").status_code == 403

    manager_client = client_for(manager, org)
    ids = {f["id"] for f in manager_client.get("/api/v1/files/").json()["results"]}
    assert standard["id"] in ids and sensitive["id"] not in ids
    assert manager_client.get(f"/api/v1/files/{sensitive['id']}/download/").status_code == 404
    assert manager_client.delete(f"/api/v1/files/{standard['id']}/").status_code == 403
    assert staff_client.delete(f"/api/v1/files/{own['id']}/").status_code == 204


def test_staff_cannot_upload_sensitive_files(new_org, add_member, client_for):
    org, _owner = new_org()
    staff, _ = add_member(org, "staff@example.com", "staff")
    response = client_for(staff, org).post(
        "/api/v1/files/",
        {
            "name": "x.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
            "classification": "sensitive",
        },
        format="json",
    )
    assert response.status_code == 403


def test_support_access_never_downloads_files(new_org, make_staff, client_for):
    org, owner = new_org()
    file = _upload(client_for(owner, org))
    staff = make_staff()
    with tenant_context(org.id):
        from django.utils import timezone

        SupportAccessGrant.objects.create(
            staff_user=staff,
            reason="r",
            duration_minutes=30,
            status="approved",
            starts_at=timezone.now(),
            expires_at=timezone.now() + timedelta(minutes=30),
        )
    support = client_for(staff, org, mfa=True)
    response = support.get(f"/api/v1/files/{file['id']}/download/")
    assert response.status_code in (403, 404)


def test_sniffing():
    assert sniff(PNG[:16]) == "image/png"
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff(b"<svg xmlns=") is None


def _fake_clamd(reply: bytes) -> tuple[int, list[bytes]]:
    received: list[bytes] = []
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        data = b""
        while not data.endswith(struct.pack("!L", 0)):
            data += conn.recv(4096)
        received.append(data)
        conn.sendall(reply)
        conn.close()
        server.close()

    threading.Thread(target=serve, daemon=True).start()
    return server.getsockname()[1], received


@pytest.mark.parametrize(
    ("reply", "clean"), [(b"stream: OK\0", True), (b"stream: Eicar-Test-Signature FOUND\0", False)]
)
def test_clamd_scanner_speaks_instream(reply, clean):
    port, received = _fake_clamd(reply)
    with override_settings(CLAMD_HOST="127.0.0.1", CLAMD_PORT=port):
        result = ClamdScanner().scan([b"hello", b"world"])
    assert result.clean is clean
    assert received[0].startswith(b"zINSTREAM\0" + struct.pack("!L", 5) + b"hello")


@override_settings(
    S3_ENDPOINT_URL="http://minio:9000",
    S3_PUBLIC_ENDPOINT_URL="http://localhost:9000",
    S3_BUCKET="tillfree-files",
    S3_ACCESS_KEY_ID="test",
    S3_SECRET_ACCESS_KEY="test-secret",
)
def test_s3_presigned_post_restricts_key_type_and_size():
    import base64
    import json

    from apps.documents.storage import S3ObjectStorage

    s3 = S3ObjectStorage()
    upload = s3.presign_upload(
        "org/1/files/2", content_type="image/png", max_bytes=1234, expires_in=300
    )
    assert upload.url.startswith(
        "http://localhost:9000/tillfree-files"
    )  # browser-reachable address
    policy = json.loads(base64.b64decode(upload.fields["policy"]))
    conditions = policy["conditions"]
    assert {"key": "org/1/files/2"} in conditions
    assert {"Content-Type": "image/png"} in conditions
    assert ["content-length-range", 1, 1234] in conditions

    url = s3.presign_download(
        "org/1/files/2", filename='evil"; x.pdf', content_type="application/pdf", expires_in=60
    )
    assert "X-Amz-Expires=60" in url and "attachment" in url
    assert "%22%3B" not in url  # quotes/semicolons stripped from the filename
