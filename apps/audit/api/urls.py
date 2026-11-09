from rest_framework.routers import SimpleRouter

from .views import AuditEventViewSet

router = SimpleRouter()
router.register("audit-events", AuditEventViewSet, basename="audit-event")
urlpatterns = router.urls
