from rest_framework.routers import SimpleRouter

from .views import FileViewSet

router = SimpleRouter()
router.register("files", FileViewSet, basename="file")
urlpatterns = router.urls
