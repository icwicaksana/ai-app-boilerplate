"""Kontrak schema stabil — test ini gagal jika ada yang ubah request/response sembarangan."""

from app.schemas.chat import ChatRequest
from app.schemas.job import JobRequest


def test_chat_request_defaults():
    req = ChatRequest(input="halo")
    assert req.agent == "example_chat"
    assert req.thread_id is None


def test_job_request_defaults():
    req = JobRequest(payload={"a": 1})
    assert req.thread_id is None
