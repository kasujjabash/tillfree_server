from django.contrib import admin

from .models import Branch, Business, Membership


class BranchInline(admin.TabularInline):
    model = Branch
    extra = 0


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "industry_pack", "is_active", "created_at")
    list_filter = ("industry_pack", "is_active")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [BranchInline, MembershipInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "business", "role", "branch", "is_active")
    list_filter = ("role", "business")
