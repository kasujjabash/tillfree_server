"""Automated tests. Uses the same PostgreSQL roles and RLS policies as production."""

from .base import *

DEBUG = False
SECRET_KEY = "testing-only-secret-key-" + "x" * 40
ALLOWED_HOSTS = ["testserver", "localhost"]
APP_ENVIRONMENT = "testing"

# Fast hashing in tests only; production uses Argon2id (see base.py).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

CELERY_BROKER_URL = "memory://"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

API_DOCS_ENABLED = True

# A throwaway tenant-scoped model used to prove the tenancy machinery end to end.
INSTALLED_APPS = [*INSTALLED_APPS, "tests.tenancy_app"]

MIDDLEWARE = [m for m in MIDDLEWARE if not m.startswith("whitenoise.")]

LOGGING = build_logging_config(json_logs=False, level="WARNING")
