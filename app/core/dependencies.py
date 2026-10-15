"""Dependency injection: satu-satunya tempat wiring settings/checkpointer/controller.

- Rel chat: route /chat/stream -> Depends(get_chat_controller) -> ChatController.
- Rel pipeline: route /jobs/{agent} -> Depends(get_job_controller) -> JobController.
"""

from fastapi import Depends, Request

from app.controllers.ChatController import ChatController
from app.controllers.JobController import JobController
from app.controllers.runner import Runner
from app.core.config import Settings, get_settings
from app.infra.postgres import get_checkpointer


def get_runner() -> Runner:
    return Runner()


def get_request_id(request: Request) -> str:
    """Satu sumber request_id: echo X-Request-ID atau generate 12 hex."""
    import uuid

    return request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]


def get_chat_controller(settings: Settings = Depends(get_settings)) -> ChatController:
    checkpointer = get_checkpointer(settings.DATABASE_URL)
    return ChatController(settings, checkpointer, get_runner())


def get_job_controller(settings: Settings = Depends(get_settings)) -> JobController:
    checkpointer = get_checkpointer(settings.DATABASE_URL)
    return JobController(settings, checkpointer, get_runner())
