import ipaddress

from django.http import HttpRequest
from rest_framework.settings import api_settings


def client_ip(request: HttpRequest) -> str | None:
    """The client's IP, trusting X-Forwarded-For only for the configured number of proxies
    (same rule DRF uses for throttling, see NUM_PROXIES)."""
    remote = request.META.get("REMOTE_ADDR")
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    candidate = remote
    if api_settings.NUM_PROXIES and forwarded:
        addresses = [a.strip() for a in forwarded.split(",") if a.strip()]
        if addresses:
            candidate = addresses[-min(api_settings.NUM_PROXIES, len(addresses))]
    try:
        return str(ipaddress.ip_address(candidate)) if candidate else None
    except ValueError:
        return None


def user_agent(request: HttpRequest) -> str:
    return str(request.META.get("HTTP_USER_AGENT", ""))[:300]
