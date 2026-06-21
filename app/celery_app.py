from importlib import import_module

from celery import Celery
from celery.signals import before_task_publish, task_postrun, task_prerun

from app.logging import request_id_context, set_log_context
from app.services.scheduler_config import get_celery_config

celery_app = Celery("dotmac_voice")
celery_app.conf.update(get_celery_config())
celery_app.conf.beat_schedule = {}
celery_app.conf.beat_scheduler = "app.celery_scheduler.DbScheduler"
celery_app.autodiscover_tasks(["app.tasks"])
import_module("app.tasks.example")


@before_task_publish.connect
def propagate_request_id(headers=None, **kwargs) -> None:
    if headers is None:
        return
    request_id = request_id_context.get()
    if request_id:
        headers.setdefault("request_id", request_id)


@task_prerun.connect
def bind_task_log_context(task=None, **kwargs) -> None:
    headers = getattr(getattr(task, "request", None), "headers", None) or {}
    set_log_context(request_id=headers.get("request_id"))


@task_postrun.connect
def clear_task_log_context(**kwargs) -> None:
    set_log_context()
