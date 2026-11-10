from common.notifications import ChannelTemplate, NotificationCategory

CATEGORIES = [
    NotificationCategory(
        key="access.member_joined",
        description="Someone joined the organisation",
        variables=("member_name", "organisation_name", "role_name"),
        templates={
            "in_app": ChannelTemplate(
                "New member", "{{ member_name }} joined {{ organisation_name }} as {{ role_name }}."
            ),
            "email": ChannelTemplate(
                "{{ member_name }} joined {{ organisation_name }}",
                "Hello,\n\n{{ member_name }} accepted their invitation and joined "
                "{{ organisation_name }} as {{ role_name }}.",
            ),
            "sms": ChannelTemplate(
                "", "Tillfree: {{ member_name }} joined {{ organisation_name }}."
            ),
        },
    ),
    NotificationCategory(
        key="access.sensitive_role_granted",
        description="Someone was given access to sensitive information",
        variables=("member_name", "role_name", "granted_by", "organisation_name", "permissions"),
        mandatory=True,
        sensitive=True,
        templates={
            "in_app": ChannelTemplate(
                "Sensitive access granted",
                "{{ granted_by }} gave {{ member_name }} the role {{ role_name }}, "
                "which includes: {{ permissions }}.",
            ),
            "email": ChannelTemplate(
                "Sensitive access granted in {{ organisation_name }}",
                'Hello,\n\n{{ granted_by }} gave {{ member_name }} the role "{{ role_name }}" in '
                "{{ organisation_name }}. It includes sensitive permissions: {{ permissions }}.\n\n"
                "You receive this because you are an Owner. If this was not expected, review the "
                "organisation's roles now.",
            ),
        },
    ),
    NotificationCategory(
        key="access.support_access",
        description="Platform support asked for, or opened, access to the organisation",
        variables=("staff_name", "reason", "duration_minutes", "organisation_name", "kind"),
        mandatory=True,
        sensitive=True,
        templates={
            "in_app": ChannelTemplate(
                "Support access {{ kind }}",
                "{{ staff_name }} (Tillfree support): {{ reason }} "
                "({{ duration_minutes }} minutes).",
            ),
            "email": ChannelTemplate(
                "Support access {{ kind }} for {{ organisation_name }}",
                "Hello,\n\nSupport access was {{ kind }} for {{ organisation_name }}.\n\n"
                "Staff member: {{ staff_name }}\nReason: {{ reason }}\n"
                "Duration: {{ duration_minutes }} minutes\n\n"
                "Review, approve or revoke it in your organisation's security settings. Support "
                "access never includes sensitive or clinical data, and every request is recorded "
                "in your audit log.",
            ),
        },
    ),
]
