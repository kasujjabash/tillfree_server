from django.conf import settings
from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.access.api import urls as access_urls
from apps.audit.api import urls as audit_urls
from apps.documents.api import urls as document_urls
from apps.identity.api import urls as identity_urls
from apps.localisation.api import urls as localisation_urls
from apps.notifications.api import urls as notification_urls
from apps.organisations.api import urls as organisation_urls
from apps.subscriptions.api import urls as subscription_urls
from common.health import views as health

# Domain routes (docs/architecture/08-api.md §1).
api_v1: list[URLPattern | URLResolver] = [
    path("auth/", include(identity_urls.auth_urlpatterns)),
    path("me/", include(identity_urls.me_urlpatterns)),
    path("", include(organisation_urls.urlpatterns)),
    path("", include(access_urls.urlpatterns)),
    path("", include(audit_urls.urlpatterns)),
    path("", include(localisation_urls.urlpatterns)),
    path("", include(subscription_urls.urlpatterns)),
    path("", include(notification_urls.urlpatterns)),
    path("", include(document_urls.urlpatterns)),
]

urlpatterns: list[URLPattern | URLResolver] = [
    path("health/live/", health.live, name="health-live"),
    path("health/ready/", health.ready, name="health-ready"),
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/v1/", include((api_v1, "v1"))),
]

if settings.API_DOCS_ENABLED:
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    ]

handler400 = "common.api.views.bad_request"
handler403 = "common.api.views.permission_denied"
handler404 = "common.api.views.not_found"
handler500 = "common.api.views.server_error"
