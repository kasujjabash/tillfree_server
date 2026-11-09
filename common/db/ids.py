"""Primary-key generation. See docs/architecture/adr/0003-uuidv7-primary-keys.md."""

import uuid


def uuid7() -> uuid.UUID:
    """Time-ordered UUID (RFC 9562 version 7): index-friendly and safe to expose."""
    return uuid.uuid7()
