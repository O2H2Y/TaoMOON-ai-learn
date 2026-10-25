"""搜索工具全部报错时，不能把 Agent 的"没找到资料"当参考资料；并要给前端提示。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.observability.run_context import is_tool_error


def _settings():
    s = MagicMock()
    s.enable_web_search = True
    s.tavily_api_key = "tvly-test"
    s.deepseek_model = "deepseek-chat"
    s.deepseek_base_url = "https://api.deepseek.com"
    s.deepseek_api_key = "sk-test"
    return s


def _agent(messages):
    agent = AsyncMock()
    agent.ainvoke.return_value = {"messages": messages}
    return agent


def _call(i):
    return AIMessage(content="", tool_calls=[{"name": "tavily_search_basic", "args": {"query": "q"}, "id": f"c{i}"}])


@pytest.mark.parametrize("content,status,expected", [
    ("{'error': Exception('Error 401: Unauthorized')}", None, True),
    ('{"error": "proxy error"}', None, True),
    ("Error: ToolException('bad args')", None, True),
    ("whatever", "error", True),
    ('{"query": "x", "results": [{"url": "https://a"}]}', None, False),
])
def test_is_tool_error(content, status, expected):
    assert is_tool_error(content, status) is expected


@pytest.mark.asyncio
async def test_all_tools_failed_is_error_and_not_fed_to_quiz():
    msgs = [
        _call(1), ToolMessage(content="{'error': Exception('Error 401: Unauthorized')}", tool_call_id="c1", name="tavily_search_basic"),
        _call(2), ToolMessage(content="{'error': Exception('Error 401: Unauthorized')}", tool_call_id="c2", name="tavily_search_deep"),
        AIMessage(content="抱歉，未找到相关资料，以下根据常识整理：……"),
    ]
    with (
        patch("app.services.search_service.get_settings", return_value=_settings()),
        patch("app.services.search_service._build_agent", return_value=(_agent(msgs), MagicMock())),
    ):
        from app.services.search_service import fetch_knowledge_context_result

        ctx = await fetch_knowledge_context_result("秦灭六国")
    assert ctx.status == "error"
    assert ctx.summary == ""
    assert ctx.tool_errors == 2
    assert ctx.error.startswith("all_tool_calls_failed")


@pytest.mark.asyncio
async def test_partial_tool_failure_still_ok():
    msgs = [
        _call(1), ToolMessage(content="{'error': Exception('timeout')}", tool_call_id="c1", name="tavily_search_basic"),
        _call(2), ToolMessage(content='{"results": [{"url": "https://a", "content": "秦于前221年统一"}]}',
                              tool_call_id="c2", name="tavily_search_basic"),
        AIMessage(content="秦于公元前221年统一六国。"),
    ]
    with (
        patch("app.services.search_service.get_settings", return_value=_settings()),
        patch("app.services.search_service._build_agent", return_value=(_agent(msgs), MagicMock())),
    ):
        from app.services.search_service import fetch_knowledge_context_result

        ctx = await fetch_knowledge_context_result("秦灭六国")
    assert ctx.status == "ok"
    assert "221" in ctx.summary
    assert ctx.tool_errors == 1


def _req():
    from app.models.quiz import QuizGenerateRequest

    return QuizGenerateRequest(user_input="秦灭六国", question_count=5)


@pytest.mark.parametrize("status,expect_notice", [("error", True), ("timeout", True), ("empty", True),
                                                   ("ok", False), ("disabled", False)])
def test_search_notice(status, expect_notice):
    from app.observability import ContextResult, record_context, start_run
    from app.services.quiz_service import SEARCH_FAILED_NOTICE, _search_notice

    with start_run("quiz"):
        record_context(ContextResult(source="web", status=status))
        notice = _search_notice(_req())
    assert (notice == SEARCH_FAILED_NOTICE) is expect_notice


def test_search_notice_none_without_run():
    from app.services.quiz_service import _search_notice

    assert _search_notice(_req()) is None
