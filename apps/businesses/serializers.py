from rest_framework import serializers

from .models import Branch, Business, Membership


class BusinessSerializer(serializers.ModelSerializer):
    class Meta:
        model = Business
        fields = ["id", "name", "slug", "industry_pack", "enabled_addons", "phone", "address", "currency"]
        read_only_fields = ["id", "slug", "industry_pack"]


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "name", "address", "is_main"]


class MembershipSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    full_name = serializers.CharField(source="user.get_full_name", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "username", "full_name", "role", "branch", "is_active"]
