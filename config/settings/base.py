"""Settings shared by every environment.

``development``, ``testing`` and ``production`` import from here and override only
what differs. All configuration comes from environment variables (see .env.example).
"""

from pathlib import Path
from typing import Any

from django.utils.translation import gettext_lazy as _
from dotenv import load_dotenv

from common.env import env_bool, env_float, env_int, env_list, env_str
from common.observability.logging import build_logging_config
from common.observability.sentry import init_sentry

BASE_DIR = Path(__file__).resolve().parents[2]

# A local .env is a development convenience. Real environment variables win.
load_dotenv(BASE_DIR / ".env", override=False)

# ------------------------------------------------------------------ core
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "")
DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
APP_ENVIRONMENT = env_str("APP_ENVIRONMENT", "development")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    # Framework-level code (no models)
    "common",
    # Domains
    "apps.identity",
    "apps.organisations",
]

MIDDLEWARE = [
    "common.health.middleware.HealthCheckMiddleware",  # first: probes skip host checks
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "common.observability.middleware.RequestIDMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# ------------------------------------------------------------------ database
def database_config(*, pool: bool) -> dict[str, Any]:
    """PostgreSQL connection settings.

    The application must connect as a role WITHOUT superuser or BYPASSRLS
    (see docs/architecture/03-multi-tenancy.md §4). ``manage.py check --deploy
    --database default`` verifies this.
    """
    statement_timeout = env_int("DB_STATEMENT_TIMEOUT_MS", 5000)
    idle_tx_timeout = env_int("DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", 10000)
    options: dict[str, Any] = {
        "options": (
            f"-c statement_timeout={statement_timeout} "
            f"-c idle_in_transaction_session_timeout={idle_tx_timeout}"
        ),
    }
    if pool:
        options["pool"] = {
            "min_size": env_int("DB_POOL_MIN_SIZE", 2),
            "max_size": env_int("DB_POOL_MAX_SIZE", 10),
            "timeout": env_int("DB_POOL_TIMEOUT_SECONDS", 10),
        }
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env_str("DB_NAME", "tilltree"),
        "USER": env_str("DB_USER", "tilltree_owner"),
        "PASSWORD": env_str("DB_PASSWORD", ""),
        "HOST": env_str("DB_HOST", "localhost"),
        "PORT": env_str("DB_PORT", "5432"),
        # The pool and persistent connections are mutually exclusive.
        "CONN_MAX_AGE": 0 if pool else env_int("DB_CONN_MAX_AGE", 60),
        "CONN_HEALTH_CHECKS": True,
        # Required when running behind PgBouncer in transaction mode.
        "DISABLE_SERVER_SIDE_CURSORS": env_bool("DB_DISABLE_SERVER_SIDE_CURSORS", False),
        "OPTIONS": options,
    }


DATABASES = {"default": database_config(pool=env_bool("DB_POOL", False))}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"  # contrib apps only; ours use UUIDv7

# ------------------------------------------------------------------ cache
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env_str("REDIS_CACHE_URL", "redis://localhost:6379/0"),
        "KEY_PREFIX": "tt",
        "TIMEOUT": 300,
    }
}

# ------------------------------------------------------------------ identity
AUTH_USER_MODEL = "identity.User"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ------------------------------------------------------------------ i18n / time
# Timestamps are stored in UTC. Business dates use the branch/organisation
# timezone (docs/architecture/06-localisation.md §5), never this setting.
TIME_ZONE = "UTC"
USE_TZ = True
USE_I18N = True
LANGUAGE_CODE = "en"
LANGUAGES = [("en", _("English"))]
LOCALE_PATHS = [BASE_DIR / "locale"]

# ------------------------------------------------------------------ static
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES: dict[str, dict[str, Any]] = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# ------------------------------------------------------------------ API
REST_FRAMEWORK: dict[str, Any] = {
    # Token authentication arrives in Phase 2; until then every endpoint denies by default.
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PAGINATION_CLASS": "common.api.pagination.DefaultCursorPagination",
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env_str("THROTTLE_ANON_RATE", "60/min"),
        "user": env_str("THROTTLE_USER_RATE", "600/min"),
    },
    # Proxies in front of the app. DRF throttling takes the client IP from X-Forwarded-For
    # this many hops from the right; unset (None) trusts the whole client-supplied header.
    "NUM_PROXIES": env_int("DRF_NUM_PROXIES", 0),
    "EXCEPTION_HANDLER": "common.api.handlers.problem_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Tilltree API",
    "DESCRIPTION": "The Operating System for Organisations.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

API_DOCS_ENABLED = env_bool("API_DOCS_ENABLED", False)
# RFC 9457 "type" URIs. A URN until a public documentation domain exists.
API_PROBLEM_TYPE_BASE = env_str("API_PROBLEM_TYPE_BASE", "urn:tilltree:problem:")
ADMIN_URL = env_str("DJANGO_ADMIN_URL", "admin/")

# ------------------------------------------------------------------ CORS / security
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "")
CORS_ALLOW_CREDENTIALS = True  # refresh-token cookie (Phase 2)
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-language",
    "authorization",
    "content-type",
    "idempotency-key",
    "x-organisation-id",
    "x-request-id",
    "x-csrftoken",
]
CORS_EXPOSE_HEADERS = ["x-request-id", "retry-after"]

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = "common.api.views.csrf_failure"
# API bodies are small; files go straight to object storage (Phase 3).
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# ------------------------------------------------------------------ Celery
CELERY_BROKER_URL = env_str("CELERY_BROKER_URL", "redis://localhost:6379/1")
CELERY_RESULT_BACKEND = None
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = env_int("CELERY_TASK_TIME_LIMIT", 300)
CELERY_TASK_SOFT_TIME_LIMIT = env_int("CELERY_TASK_SOFT_TIME_LIMIT", 240)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULE: dict[str, Any] = {}

# ------------------------------------------------------------------ observability
LOGGING_CONFIG = "logging.config.dictConfig"
LOGGING = build_logging_config(
    json_logs=env_bool("LOG_JSON", True), level=env_str("LOG_LEVEL", "INFO")
)

SENTRY_DSN = env_str("SENTRY_DSN", "")
if SENTRY_DSN:
    init_sentry(
        dsn=SENTRY_DSN,
        environment=APP_ENVIRONMENT,
        release=env_str("APP_RELEASE", "") or None,
        traces_sample_rate=env_float("SENTRY_TRACES_SAMPLE_RATE", 0.0),
    )
