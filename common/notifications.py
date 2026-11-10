"""Notification category declarations, shared by every domain.

Each domain declares what it may notify people about in ``<app>/notifications.py``::

    CATEGORIES = [
        NotificationCategory(
            key="access.member_joined",
            description="Someone joined the organisation",
            variables=("member_name", "organisation_name"),
            templates={
                "in_app": ChannelTemplate("New member", "{{ member_name }} joined."),
                "email": ChannelTemplate("New member in {{ organisation_name }}", "..."),
            },
        ),
    ]

Templates use ``{{ variable }}`` placeholders only (no logic), so organisations can
safely write their own versions.
"""

from dataclasses import dataclass, field

CHANNELS = ("in_app", "email", "sms")


@dataclass(frozen=True)
class ChannelTemplate:
    subject: str  # email subject / in-app title; ignored for SMS
    body: str


@dataclass(frozen=True)
class NotificationCategory:
    key: str
    description: str
    variables: tuple[str, ...]
    templates: dict[str, ChannelTemplate]
    default_channels: tuple[str, ...] = ("in_app", "email")
    mandatory: bool = False  # security notices: cannot be switched off
    sensitive: bool = False  # never sent by SMS (shared phones, lock screens)
    tags: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        unknown = set(self.templates) - set(CHANNELS)
        if unknown:
            raise ValueError(f"{self.key}: unknown channels {sorted(unknown)}")
        for channel in self.default_channels:
            if channel not in self.templates:
                raise ValueError(f"{self.key}: default channel {channel!r} has no template")
