"""Local development.

Works without Redis: the cache falls back to memory and Celery runs tasks inline.
"""

import os

from .base import *

DEBUG = True
SECRET_KEY = SECRET_KEY or "dev-insecure-key-never-use-in-production"
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
