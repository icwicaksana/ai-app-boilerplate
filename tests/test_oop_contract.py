"""Kontrak OOP: ABC, AgentContext, registry (pengaman refaktor)."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.AgentContext import AgentContext
from app.agents.BaseChatAgent import BaseChatAgent
from app.agents.BasePipelineAgent import BasePipelineAgent
from app.agents.ExampleChatAgent import ExampleChatAgent
from app.agents.ExamplePipelineAgent import ExamplePipelineAgent
from app.controllers.ChatController import ChatController
from app.controllers.JobController import JobController
from app.controllers.registry import CHAT_AGENTS, JOB_AGENTS
from app.controllers.runner import Runner
from app.core.config import Settings
from app.core.errors import AgentNotFoundError


def _settings():
    return Settings(_env_file=None, MODEL_PROVIDER="openai", OPENAI_API_KEY="x")


def _ctx():
    return AgentContext(settings=_settings(), checkpointer=InMemorySaver(), runner=Runner())


def test_base_pipeline_abstract():
    with pytest.raises(TypeError):
        BasePipelineAgent(_ctx())


def test_template_pipeline_abstract():
    from app.agents.TemplatePipelineAgent import TemplatePipelineAgent

    with pytest.raises(TypeError):
        TemplatePipelineAgent(_ctx())


def test_example_pipeline_via_context():
    ag = ExamplePipelineAgent(_ctx())
    assert ag.graph is not None
    assert ag.llm is not None


def test_example_chat_via_context():
    ag = ExampleChatAgent(_ctx())
    assert ag.graph is not None


def test_context_holds_refs():
    ctx = _ctx()
    assert ctx.session_id is None
    ctx2 = AgentContext(settings=ctx.settings, checkpointer=ctx.checkpointer,
                        runner=ctx.runner, session_id="thr-abc")
    assert ctx2.session_id == "thr-abc"
    assert ctx2.runner is ctx.runner


def test_registry_contents():
    assert CHAT_AGENTS["example_chat"] is ExampleChatAgent
    assert JOB_AGENTS["example_pipeline"] is ExamplePipelineAgent
    assert issubclass(ExampleChatAgent, BaseChatAgent)
    assert issubclass(ExamplePipelineAgent, BasePipelineAgent)


def test_controllers_use_registry():
    chat = ChatController(_settings(), InMemorySaver(), Runner())
    chat._ensure_agent("example_chat")
    with pytest.raises(AgentNotFoundError):
        chat._ensure_agent("tidak_ada")
    with pytest.raises(AgentNotFoundError):
        chat._ensure_agent("example_pipeline")  # rel salah → 404, bukan nyasar

    job = JobController(_settings(), InMemorySaver(), Runner())
    job._ensure_agent("example_pipeline")
    with pytest.raises(AgentNotFoundError):
        job._ensure_agent("example_chat")

    assert isinstance(chat._agent_for("thr-1", "example_chat"), ExampleChatAgent)
    assert isinstance(job._agent_for("run-1", "example_pipeline"), ExamplePipelineAgent)
