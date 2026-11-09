"""Only platform-level records are administered here; tenant data never is."""

from django.contrib import admin

from .models import PlatformStaff


@admin.register(PlatformStaff)
class PlatformStaffAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "is_active", "created_at")
    list_filter = ("role", "is_active")
    search_fields = ("user__email",)
    autocomplete_fields = ("user",)
