from rest_framework import serializers

from apps.businesses.models import Membership

from .models import User


class MembershipBriefSerializer(serializers.ModelSerializer):
    business_slug = serializers.CharField(source="business.slug")
    business_name = serializers.CharField(source="business.name")
    industry_pack = serializers.CharField(source="business.industry_pack")

    class Meta:
        model = Membership
        fields = ["business_slug", "business_name", "industry_pack", "role", "branch"]


class MeSerializer(serializers.ModelSerializer):
    memberships = MembershipBriefSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "phone", "memberships"]
        read_only_fields = ["id", "username", "memberships"]
