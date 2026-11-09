"""Django admin is for platform staff and global data only.

Tenant-owned models must never be registered here: admin bypasses tenant enforcement
(docs/architecture/03-multi-tenancy.md §4). A test enforces this.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm
from django.contrib.auth.forms import UserChangeForm as DjangoUserChangeForm
from django.utils.translation import gettext_lazy as _

from .models import User


class UserCreationForm(BaseUserCreationForm):
    class Meta:
        model = User
        fields = ("email",)


class UserChangeForm(DjangoUserChangeForm):
    class Meta:
        model = User
        fields = (
            "email",
            "password",
            "full_name",
            "preferred_name",
            "phone",
            "locale",
            "timezone",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
            "user_permissions",
        )


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    ordering = ("email",)
    list_display = ("email", "full_name", "is_active", "is_staff", "created_at")
    list_filter = ("is_active", "is_staff", "is_superuser")
    search_fields = ("email", "full_name", "phone")
    readonly_fields = (
        "last_login",
        "created_at",
        "updated_at",
        "email_verified_at",
        "phone_verified_at",
    )
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Profile"), {"fields": ("full_name", "preferred_name", "phone", "locale", "timezone")}),
        (_("Verification"), {"fields": ("email_verified_at", "phone_verified_at")}),
        (
            _("Access"),
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Dates"), {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)
