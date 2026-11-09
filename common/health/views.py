"""Health endpoints for load balancers and orchestrators.

``live``  - the process can serve HTTP. No dependency checks, so a database outage
            does not cause every container to be restarted.
``ready`` - dependencies are reachable; the instance may receive traffic.

Responses never include dependency details; failures are logged instead.
"""

import structlog
from django.core.cache import cache
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = structlog.get_logger("tilltree.health")


def check_database() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except Exception:
        logger.warning("readiness_database_failed", exc_info=True)
        return False


def check_cache() -> bool:
    try:
        cache.set("health:ready", "1", timeout=10)
        return bool(cache.get("health:ready") == "1")
    except Exception:
        logger.warning("readiness_cache_failed", exc_info=True)
        return False


@never_cache
@require_GET
def live(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@never_cache
@require_GET
def ready(request: HttpRequest) -> JsonResponse:
    if check_database() and check_cache():
        return JsonResponse({"status": "ok"})
    return JsonResponse({"status": "unavailable"}, status=503)
