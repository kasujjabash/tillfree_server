"""Production. Values come from the environment / secrets manager; unsafe defaults are refused."""

from django.core.exceptions import ImproperlyConfigured

from .base import *

DEBUG = False

if len(SECRET_KEY) < 50 or SECRET_KEY.startswith(("dev-", "testing-")):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be a random value of at least 50 characters."
    )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set in production.")

# A connection pool is the default under ASGI, where persistent connections are not reused.
DATABASES = {"default": database_config(pool=env_bool("DB_POOL", True))}

# TLS is terminated at the load balancer.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
REST_FRAMEWORK["NUM_PROXIES"] = env_int("DRF_NUM_PROXIES", 1)
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
SECURE_REDIRECT_EXEMPT = [r"^health/"]  # internal load-balancer probes
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 60 * 60 * 24 * 30)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False  # enable once the domain setup is stable
SILENCED_SYSTEM_CHECKS = ["security.W021"]  # the preload decision above is deliberate
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

STORAGES["staticfiles"] = {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}
