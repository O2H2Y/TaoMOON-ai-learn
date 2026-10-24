"""可观测性（RunRecord / ContextResult / tracing）测试：只记录，不改变业务行为。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.observability import (
    current_run,
    record_llm_usage,
    record_retrieval,
    stage,
    start_run,
)
from app.observability.run_context import extract_agent_trace
from app.observability.tracing import build_langchain_config


def _agent_messages():
    return [
        HumanMessage(content="RAG"),
        AIMessage(
            content="",
            tool_calls=[{"name": "tavily_search_basic", "args": {"query": "RAG"}, "id": "c1"}],
            usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        ),
        ToolMessage(content="RAG = Retrieval-Augmented Generation", name="tavily_search_basic", tool_call_id="c1"),
        AIMessage(
            content="知识摘要：RAG 是检索增强生成",
            usage_metadata={"input_tokens": 300, "output_tokens": 50, "total_tokens": 350},
        ),
    ]


def test_recorders_are_noop_without_active_run():
    assert current_run() is None
    record_llm_usage("x", "m", _agent_messages())  # 不抛异常
    with stage("x"):
        pass
    assert current_run() is None


def test_usage_and_stage_are_aggregated():
    with start_run("quiz", source="web") as run:
        with stage("web_search"):
            record_llm_usage("web_search_agent", "deepseek-chat", _agent_messages())
        record_llm_usage("quiz_generate", "deepseek-chat", AIMessage(
            content="{}", usage_metadata={"input_tokens": 1000, "output_tokens": 800, "total_tokens": 1800}
        ))
    totals = run.total_tokens()
    assert totals == {"input_tokens": 1400, "output_tokens": 870, "total_tokens": 2270, "llm_calls": 3}
    assert "web_search" in run.stage_ms()
    assert current_run() is None


def test_nested_start_run_reuses_outer_record():
    with start_run("quiz", dataset="dev") as outer:
        with start_run("quiz", mode="sync") as inner:
            assert inner is outer
    assert outer.metadata == {"dataset": "dev", "mode": "sync"}


def test_extract_agent_trace_keeps_tool_calls_and_evidence():
    tool_calls, evidence = extract_agent_trace(_agent_messages())
    assert tool_calls == [{"name": "tavily_search_basic", "args": {"query": "RAG"}}]
    assert evidence[0]["tool"] == "tavily_search_basic"
    assert "Retrieval-Augmented" in evidence[0]["content"]


def test_record_retrieval_keeps_chunk_ids():
    docs = [Document(page_content="chunk-1", metadata={"doc_id": "d1", "page": 2}, id="vec-1")]
    with start_run("quiz") as run:
        record_retrieval("什么是 RAG", 4, docs, doc_id="d1")
    assert run.retrievals[0]["results"][0]["id"] == "vec-1"
    assert run.retrievals[0]["results"][0]["metadata"]["page"] == 2


def test_build_langchain_config_without_langfuse_has_no_callbacks():
    with patch("app.observability.tracing.get_trace_callbacks", return_value=[]):
        cfg = build_langchain_config("quiz_generate", recursion_limit=10)
    assert "callbacks" not in cfg
    assert cfg["recursion_limit"] == 10
    assert cfg["metadata"]["prompt_versions"]["quiz"].startswith("quiz-")


def test_build_langchain_config_links_session_to_run_id():
    with patch("app.observability.tracing.get_trace_callbacks", return_value=[]):
        with start_run("quiz") as run:
            cfg = build_langchain_config("quiz_generate")
    assert cfg["metadata"]["langfuse_session_id"] == run.run_id


@pytest.mark.asyncio
async def test_search_service_records_context_result_with_evidence():
    settings = MagicMock()
    settings.enable_web_search = True
    settings.tavily_api_key = "tvly-test"
    settings.deepseek_model = "deepseek-chat"
    long_summary = "摘" * 6000
    messages = _agent_messages()
    messages[-1] = AIMessage(content=long_summary, usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    agent = AsyncMock()
    agent.ainvoke.return_value = {"messages": messages}

    with (
        patch("app.services.search_service.get_settings", return_value=settings),
        patch("app.services.search_service._build_agent", return_value=(agent, MagicMock())),
    ):
        from app.services.search_service import MAX_CONTEXT_LENGTH, fetch_knowledge_context

        with start_run("quiz") as run:
            text = await fetch_knowledge_context("RAG")

    assert len(text) == MAX_CONTEXT_LENGTH  # 行为不变：仍截断
    ctx = run.contexts[0]
    assert ctx.source == "web" and ctx.status == "ok"
    assert ctx.truncated is True and ctx.raw_summary_length == 6000
    assert ctx.evidence and ctx.tool_calls
    assert run.total_tokens()["llm_calls"] == 2  # agent 内两次 LLM 调用


@pytest.mark.asyncio
async def test_quiz_service_persists_prompt_version_and_trace_id(sample_quiz_response_data):
    from app.models.quiz import QuizGenerateRequest, QuizOutput
    from app.services import quiz_service

    quiz_output = QuizOutput.model_validate(
        {k: sample_quiz_response_data[k] for k in ("title", "summary", "questions")}
    )
    with (
        patch("app.services.quiz_service.check_content", return_value=True),
        patch("app.services.quiz_service.fetch_knowledge_context", AsyncMock(return_value="ctx")),
        patch("app.services.quiz_service.generate_quiz", AsyncMock(return_value=quiz_output)),
        patch("app.services.quiz_service.quiz_repository") as repo,
    ):
        repo.save_quiz_session = AsyncMock()
        await quiz_service.handle_quiz_generate(QuizGenerateRequest(user_input="RAG"), user_id=1)

    kwargs = repo.save_quiz_session.call_args.kwargs
    assert kwargs["prompt_version"].startswith("quiz-v")
    assert kwargs["trace_id"].startswith("run_")
