"""Local development.

Works without Redis: the cache falls back to memory and Celery runs tasks inline.
"""

import os

from .base import *
from .base import FIELD_ENCRYPTION_KEYS, JWT_SIGNING_KEYS, fill_dev_keys

DEBUG = True
SECRET_KEY = SECRET_KEY or "dev-insecure-key-never-use-in-production"
if not JWT_SIGNING_KEYS or not FIELD_ENCRYPTION_KEYS:
    _jwt, _fernet = fill_dev_keys(SECRET_KEY)
    JWT_SIGNING_KEYS = JWT_SIGNING_KEYS or _jwt
    FIELD_ENCRYPTION_KEYS = FIELD_ENCRYPTION_KEYS or _fernet
JWT_ACTIVE_KEY_ID = next(iter(JWT_SIGNING_KEYS))
REFRESH_COOKIE_SECURE = env_bool("REFRESH_COOKIE_SECURE", False)  # http://localhost
EMAIL_BACKEND = env_str("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
API_DOCS_ENABLED = env_bool("API_DOCS_ENABLED", True)

REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]

LOGGING = build_logging_config(
    json_logs=env_bool("LOG_JSON", False), level=env_str("LOG_LEVEL", "INFO")
)

if not os.environ.get("REDIS_CACHE_URL"):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

if not os.environ.get("CELERY_BROKER_URL"):
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True
