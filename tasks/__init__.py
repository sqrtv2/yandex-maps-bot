"""
Background tasks for Yandex Maps Profile Visitor system.
"""
from .celery_app import celery_app, app
from .warmup import (
    warmup_profile_task,
    warmup_multiple_profiles_task,
    advanced_warmup_task,
    schedule_profile_warmup,
    get_warmup_status
)
from .yandex_maps import (
    visit_yandex_maps_profile_task,
    batch_visit_yandex_profiles_task,
    validate_yandex_maps_url
)
from .yandex_search import yandex_search_click_task

__all__ = [
    "celery_app",
    "app",
    "warmup_profile_task",
    "warmup_multiple_profiles_task",
    "advanced_warmup_task",
    "schedule_profile_warmup",
    "get_warmup_status",
    "visit_yandex_maps_profile_task",
    "batch_visit_yandex_profiles_task",
    "validate_yandex_maps_url",
    "yandex_search_click_task"
]