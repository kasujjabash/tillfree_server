"""Gunicorn configuration: ASGI via the uvicorn-worker package."""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
worker_class = "uvicorn_worker.UvicornWorker"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
# Must be at least the load balancer's connection-draining time.
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = 5
# Recycle workers periodically to contain slow memory growth.
max_requests = 2000
max_requests_jitter = 200
# Only trust X-Forwarded-* from the load balancer's addresses.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
# Requests are logged by RequestIDMiddleware in structured form.
accesslog = None
errorlog = "-"
