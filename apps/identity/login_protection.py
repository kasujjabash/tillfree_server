"""Per-account brute-force protection with progressive delay.

After ``FAILURE_THRESHOLD`` failed sign-ins for one email address, further attempts
are refused for an exponentially growing period (30 s, 60 s, 120 s ... capped at 15
minutes). There is no permanent lockout, so an attacker cannot lock a real user out
for long. The counter is keyed by a hash of the email, so it also applies to
addresses that have no account (no account enumeration). Per-IP limits are separate
(DRF throttles on the login endpoint).
"""

import hashlib
import math
import time
from typing import Any

from django.core.cache import cache

FAILURE_THRESHOLD = 5
BASE_DELAY_SECONDS = 30
MAX_DELAY_SECONDS = 15 * 60
WINDOW_SECONDS = 60 * 60


def _key(email: str) -> str:
    return "auth:login-fail:" + hashlib.sha256(email.encode()).hexdigest()


def seconds_until_allowed(email: str) -> int:
    data: dict[str, Any] | None = cache.get(_key(email))
    if not data:
        return 0
    return max(0, math.ceil(float(data.get("blocked_until", 0)) - time.time()))


def record_failure(email: str) -> None:
    key = _key(email)
    data: dict[str, Any] = cache.get(key) or {"count": 0, "blocked_until": 0}
    data["count"] = int(data["count"]) + 1
    if data["count"] >= FAILURE_THRESHOLD:
        delay = min(
            BASE_DELAY_SECONDS * 2 ** (data["count"] - FAILURE_THRESHOLD), MAX_DELAY_SECONDS
        )
        data["blocked_until"] = time.time() + delay
    cache.set(key, data, WINDOW_SECONDS)


def clear(email: str) -> None:
    cache.delete(_key(email))
