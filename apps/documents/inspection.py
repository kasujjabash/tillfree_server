"""Checking what an uploaded file really is, and scanning it for malware.

The declared content type comes from the client and cannot be trusted: the first bytes
of the file decide. Scanners are pluggable (``FILE_SCANNER``); ClamAV via ``clamd`` is
provided for production.
"""

import socket
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.utils.module_loading import import_string

# Allowed types and their signatures (magic numbers).
SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "application/pdf": (b"%PDF-",),
}


def sniff(head: bytes) -> str | None:
    """The real type of a file from its first bytes, if it is an allowed type."""
    for content_type, prefixes in SIGNATURES.items():
        if any(head.startswith(prefix) for prefix in prefixes):
            return content_type
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    signature: str = ""


class Scanner(Protocol):
    def scan(self, chunks: Iterable[bytes]) -> ScanResult: ...


class NoScanner:
    """Development and tests only. Production must configure a real scanner."""

    def scan(self, chunks: Iterable[bytes]) -> ScanResult:
        for _chunk in chunks:
            pass
        return ScanResult(clean=True)


class ClamdScanner:
    """ClamAV daemon over TCP using the INSTREAM protocol."""

    def scan(self, chunks: Iterable[bytes]) -> ScanResult:
        with socket.create_connection(
            (settings.CLAMD_HOST, settings.CLAMD_PORT), timeout=30
        ) as conn:
            conn.sendall(b"zINSTREAM\0")
            for chunk in chunks:
                if chunk:
                    conn.sendall(struct.pack("!L", len(chunk)) + chunk)
            conn.sendall(struct.pack("!L", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                part = conn.recv(4096)
                if not part:
                    break
                reply += part
        text = reply.rstrip(b"\0").decode(errors="replace")
        if text.endswith("OK"):
            return ScanResult(clean=True)
        if text.endswith("FOUND"):
            return ScanResult(
                clean=False, signature=text.split(":", 1)[-1].replace("FOUND", "").strip()[:100]
            )
        raise ConnectionError(f"Unexpected clamd reply: {text[:100]}")


def scanner() -> Scanner:
    instance: Scanner = import_string(settings.FILE_SCANNER)()
    return instance
