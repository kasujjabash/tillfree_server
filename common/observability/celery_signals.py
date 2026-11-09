"""Carries the request ID from the code that queues a task into the worker that runs it."""

from typing import Any

import structlog
from celery import Task
from celery.signals import before_task_publish, task_postrun, task_prerun

REQUEST_ID_HEADER = "request_id"


@before_task_publish.connect(weak=False)
def add_request_id_header(headers: dict[str, Any] | None = None, **_: Any) -> None:
    if headers is None:
        return
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    if request_id and REQUEST_ID_HEADER not in headers:
        headers[REQUEST_ID_HEADER] = request_id


@task_prerun.connect(weak=False)
def bind_task_context(task_id: str | None = None, task: Task | None = None, **_: Any) -> None:
    if task is None or task.request.is_eager:
        return  # eager tasks run inside the caller's context; leave it alone
    structlog.contextvars.clear_contextvars()
    context: dict[str, Any] = {"task_id": task_id, "task_name": task.name}
    if request_id := getattr(task.request, REQUEST_ID_HEADER, None):
        context["request_id"] = request_id
    structlog.contextvars.bind_contextvars(**context)


@task_postrun.connect(weak=False)
def clear_task_context(task: Task | None = None, **_: Any) -> None:
    if task is not None and not task.request.is_eager:
        structlog.contextvars.clear_contextvars()
