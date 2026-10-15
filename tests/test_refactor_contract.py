"""Kontrak: BaseChatAgent inheritance + response builders + error shape."""

from app.agents.BaseChatAgent import BaseChatAgent
from app.agents.BasePipelineAgent import BasePipelineAgent
from app.agents.ExampleChatAgent import ExampleChatAgent
from app.agents.ExamplePipelineAgent import ExamplePipelineAgent
from app.agents.TemplateChatAgent import TemplateChatAgent
from app.agents.TemplatePipelineAgent import TemplatePipelineAgent
from app.helpers.response import job_error, stream_error, stream_success


def test_chat_inheritance():
    assert issubclass(ExampleChatAgent, BaseChatAgent)
    assert issubclass(TemplateChatAgent, BaseChatAgent)
    assert ExampleChatAgent.AGENT_NAME == "example_chat"
    assert TemplateChatAgent.AGENT_NAME == "template_chat"
    # TOOLS resolve berbeda (global vs copy) tapi isi sama awal
    assert list(ExampleChatAgent.TOOLS) == list(TemplateChatAgent.TOOLS)


def test_pipeline_inheritance():
    assert issubclass(ExamplePipelineAgent, BasePipelineAgent)
    assert issubclass(TemplatePipelineAgent, BasePipelineAgent)
    assert ExamplePipelineAgent.AGENT_NAME == "example_pipeline"


def test_safe_postprocess_fallback():
    class BadAgent(BaseChatAgent):
        AGENT_NAME = "bad"
        SYSTEM_PROMPT = "x"
        TOOLS = []

        def postprocess(self, reply: str):
            raise ValueError("rusak")

    # tanpa init (tidak butuh LLM): panggil unbound
    out = BaseChatAgent._safe_postprocess(BadAgent.__new__(BadAgent), "halo")
    assert out == {"reply": "halo"}


def test_stream_success_shape():
    evt = stream_success(output={"reply": "hi"})
    assert evt["type"] == "result"
    assert evt["output"] == {"reply": "hi"}
    assert evt["status"] == "completed"


def test_error_rfc_shape():
    body = job_error(title="X", detail="Y", status=404, code="NOT_FOUND", instance="req-1")
    assert body["type"] == "about:blank"
    assert body["status"] == 404
    assert body["instance"] == "req-1"
    assert body["code"] == "NOT_FOUND"
    serr = stream_error(detail="putus", request_id="req-2")
    assert serr["type"] == "error"
    assert serr["instance"] == "req-2"
