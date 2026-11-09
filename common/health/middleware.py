from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from . import views

_PROBES: dict[str, Callable[[HttpRequest], HttpResponse]] = {
    "/health/live/": views.live,
    "/health/ready/": views.ready,
}


class HealthCheckMiddleware:
    """Answers load-balancer probes before host validation and HTTPS redirects.

    Probes usually address the instance by its private IP, which is not in
    ALLOWED_HOSTS, so they must be served before anything calls request.get_host().
    The responses contain no data, so skipping host validation is safe. Must be first
    in MIDDLEWARE.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        probe = _PROBES.get(request.path)
        if probe is not None:
            return probe(request)
        return self.get_response(request)
