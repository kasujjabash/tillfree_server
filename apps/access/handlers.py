"""Reactions to access events. Run in the event's tenant context (see apps.events)."""

from apps.events.public import DomainEvent, on_event
from apps.notifications.public import notify
from apps.organisations.public import get_organisation

from . import selectors
from .models import Role, RoleAssignment, SupportAccessGrant


def _org_name(event: DomainEvent) -> str:
    organisation = get_organisation(event.organisation_id) if event.organisation_id else None
    return organisation.name if organisation else ""


@on_event("access.member_joined")
def notify_member_managers(event: DomainEvent) -> None:
    membership = selectors.get_membership(event.payload["membership_id"])
    if membership is None or event.organisation_id is None:
        return
    assignment = membership.role_assignments.first()
    role_name = (
        selectors.role_label(template_key=assignment.template_key, role=assignment.role)
        if assignment
        else ""
    )
    recipients = [
        u for u in selectors.users_holding("access.membership.manage") if u.id != membership.user_id
    ]
    notify(
        event.organisation_id,
        recipients=recipients,
        category="access.member_joined",
        context={
            "member_name": membership.user.get_full_name(),
            "organisation_name": _org_name(event),
            "role_name": role_name,
        },
        data={"membership_id": str(membership.id)},
        dedupe_key=f"event:{event.id}",
    )


@on_event("access.sensitive_role_granted")
def notify_owners_of_sensitive_grant(event: DomainEvent) -> None:
    if event.organisation_id is None:
        return
    payload = event.payload
    role = (
        Role.objects.filter(pk=payload.get("role_id")).first() if payload.get("role_id") else None
    )
    assignment = (
        RoleAssignment.objects.select_related("membership__user")
        .filter(pk=payload["assignment_id"])
        .first()
        if payload.get("assignment_id")
        else None
    )
    member_name = (
        assignment.membership.user.get_full_name() if assignment else "members holding this role"
    )
    granter = selectors.get_user(payload.get("granted_by"))
    notify(
        event.organisation_id,
        recipients=selectors.owner_users(),
        category="access.sensitive_role_granted",
        context={
            "member_name": member_name,
            "role_name": payload.get("role_name", role.name if role else ""),
            "granted_by": granter.get_full_name() if granter else "",
            "organisation_name": _org_name(event),
            "permissions": ", ".join(payload.get("permissions", [])),
        },
        dedupe_key=f"event:{event.id}",
    )


@on_event("access.support_access")
def notify_support_approvers(event: DomainEvent) -> None:
    if event.organisation_id is None:
        return
    grant = (
        SupportAccessGrant.objects.select_related("staff_user")
        .filter(pk=event.payload["grant_id"])
        .first()
    )
    if grant is None:
        return
    recipients = {u.id: u for u in selectors.users_holding("access.support.manage")}
    recipients.update({u.id: u for u in selectors.owner_users()})
    notify(
        event.organisation_id,
        recipients=recipients.values(),
        category="access.support_access",
        context={
            "staff_name": grant.staff_user.get_full_name(),
            "reason": grant.reason,
            "duration_minutes": grant.duration_minutes,
            "organisation_name": _org_name(event),
            "kind": "opened (emergency)" if grant.break_glass else "requested",
        },
        data={"support_grant_id": str(grant.id)},
        dedupe_key=f"event:{event.id}",
    )
