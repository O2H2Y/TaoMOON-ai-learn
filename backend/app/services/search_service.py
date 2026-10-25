"""知识获取服务 - 使用 AI Agent 联网搜索获取最新知识"""

import asyncio
import time

import structlog

from app.core.config import get_settings
from app.observability import ContextResult, build_langchain_config, record_context, record_llm_usage, stage
from app.observability.run_context import extract_agent_trace
from app.prompts.search_prompt import SEARCH_AGENT_SYSTEM_PROMPT

logger = structlog.get_logger()

MAX_CONTEXT_LENGTH = 5000
AGENT_TIMEOUT_SECONDS = 120


def _build_agent():
    """构建知识获取 Agent（带 3 个 Tavily 工具）"""
    from langchain_core.callbacks import AsyncCallbackHandler
    from langgraph.prebuilt import create_react_agent
    from langchain_openai import ChatOpenAI

    from app.llm.langchain_factory import deepseek_extra_body
    from langchain_tavily import TavilyExtract, TavilySearch

    settings = get_settings()

    class _ToolLogger(AsyncCallbackHandler):
        """记录每个工具调用的参数和结果"""

        async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
            tool_name = serialized.get("name", "unknown")
            logger.info("tool_call_start", tool=tool_name, input=str(input_str)[:500])

        async def on_tool_end(self, output, *, run_id, **kwargs):
            logger.info("tool_call_end", output_length=len(str(output)), output_preview=str(output)[:500])

        async def on_tool_error(self, error, *, run_id, **kwargs):
            logger.warning("tool_call_error", error=str(error)[:300])

        async def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
            logger.debug("llm_call_start", model=serialized.get("kwargs", {}).get("model", "unknown"))

        async def on_llm_end(self, response, *, run_id, **kwargs):
            # 打印 LLM 返回的 tool_calls 决策
            try:
                gen = response.generations[0][0]
                msg = getattr(gen, "message", None)
                if msg and getattr(msg, "tool_calls", None):
                    logger.info("llm_tool_decision", tool_calls=[
                        {"name": tc["name"], "args": str(tc["args"])[:200]}
                        for tc in msg.tool_calls
                    ])
                else:
                    content = getattr(msg, "content", "") if msg else str(gen)[:200]
                    logger.debug("llm_call_end", content_preview=str(content)[:200])
            except Exception:
                logger.debug("llm_call_end", raw=str(response)[:200])

    tool_logger = _ToolLogger()

    # 轻量搜索：摘要，快速
    tavily_search_basic = TavilySearch(
        name="tavily_search_basic",
        description="轻量搜索，返回最多 10 条结果的摘要。适合首轮广泛搜索，快速了解主题全貌，帮助判断正确领域。",
        max_results=10,
        include_raw_content=False,
        include_answer=False,
        tavily_api_key=settings.tavily_api_key,
    )

    # 深度搜索：完整内容，深入
    tavily_search_deep = TavilySearch(
        name="tavily_search_deep",
        description="深度搜索，返回最多 10 条结果的完整页面内容。适合深入了解专业、小众或容易混淆的主题，获取详细知识。",
        max_results=10,
        include_raw_content=True,
        include_answer=False,
        tavily_api_key=settings.tavily_api_key,
    )

    # URL 内容提取
    tavily_extract = TavilyExtract(
        name="tavily_extract",
        description="从指定 URL 提取页面完整内容。当用户输入包含网址时使用。",
        extract_depth="basic",
        tavily_api_key=settings.tavily_api_key,
    )

    tools = [tavily_search_basic, tavily_search_deep, tavily_extract]

    llm = ChatOpenAI(
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        temperature=0.1,
        extra_body=deepseek_extra_body(),
        callbacks=[tool_logger],
    )

    agent = create_react_agent(
        llm,
        tools=tools,
        prompt=SEARCH_AGENT_SYSTEM_PROMPT,
    )

    return agent, tool_logger


async def fetch_knowledge_context(user_input: str) -> str:
    """获取知识上下文：使用 AI Agent 联网搜索/提取最新知识。

    Args:
        user_input: 用户输入的学习内容（可能是关键词、一段话或 URL）

    Returns:
        知识摘要字符串，失败时返回空字符串
    """
    result = await fetch_knowledge_context_result(user_input)
    return result.summary


async def fetch_knowledge_context_result(user_input: str) -> ContextResult:
    """与 fetch_knowledge_context 行为一致，但返回包含原始证据的 ContextResult。

    返回前会写入当前 RunRecord（若有），供离线评测计算 faithfulness / 截断损失 / 失败归因。
    """
    ctx = await _fetch(user_input)
    record_context(ctx)
    return ctx


async def _fetch(user_input: str) -> ContextResult:
    settings = get_settings()

    # 配置开关检查
    if not settings.enable_web_search:
        logger.info("web_search_disabled")
        return ContextResult(source="web", status="disabled", max_length=MAX_CONTEXT_LENGTH)

    if not settings.tavily_api_key:
        logger.warning("tavily_api_key_not_configured")
        return ContextResult(source="web", status="disabled", max_length=MAX_CONTEXT_LENGTH, error="no_tavily_key")

    t1 = time.monotonic()
    try:
        logger.info("search_agent_starting", user_input=user_input[:100])

        t0 = time.monotonic()
        agent, tool_logger = _build_agent()
        logger.debug("search_agent_built", elapsed_ms=round((time.monotonic() - t0) * 1000))

        # 使用 asyncio.wait_for 设置超时
        t1 = time.monotonic()
        with stage("web_search"):
            result = await asyncio.wait_for(
                agent.ainvoke(
                    {"messages": [{"role": "user", "content": user_input}]},
                    config=build_langchain_config(
                        "web_search_agent",
                        extra_callbacks=[tool_logger],
                        recursion_limit=10,
                    ),
                ),
                timeout=AGENT_TIMEOUT_SECONDS,
            )
        elapsed = round((time.monotonic() - t1) * 1000)

        # 打印 Agent 完整消息链路用于调试
        messages = result.get("messages", [])
        logger.info(
            "search_agent_completed",
            elapsed_ms=elapsed,
            message_count=len(messages),
            message_types=[type(m).__name__ for m in messages],
        )
        for i, msg in enumerate(messages):
            msg_type = type(msg).__name__
            content_preview = str(getattr(msg, "content", ""))[:200]
            tool_calls = getattr(msg, "tool_calls", None)
            logger.debug(
                "search_agent_message",
                index=i,
                type=msg_type,
                content_preview=content_preview,
                tool_calls=len(tool_calls) if isinstance(tool_calls, list) else 0,
            )

        record_llm_usage("web_search_agent", settings.deepseek_model, messages)
        tool_calls, evidence = extract_agent_trace(messages)
        tool_errors = sum(1 for e in evidence if e.get("is_error"))

        # 所有工具调用都报错：Agent 的最终回复只是"没找到资料"之类的话，不是真实资料。
        # 判为失败，不把它当参考资料喂给出题模型（出题降级为不联网，与未开启搜索时一致）。
        if evidence and tool_errors == len(evidence):
            first_err = str(evidence[0].get("content", ""))[:300]
            logger.warning("search_all_tools_failed", tool_errors=tool_errors, first_error=first_err)
            return ContextResult(
                source="web", status="error", max_length=MAX_CONTEXT_LENGTH,
                tool_calls=tool_calls, evidence=evidence, elapsed_ms=elapsed,
                tool_errors=tool_errors, error=f"all_tool_calls_failed: {first_err}",
            )

        # 提取 Agent 最终回复
        raw = result["messages"][-1].content
        if not raw:
            logger.warning("search_agent_empty_response")
            return ContextResult(
                source="web", status="empty", max_length=MAX_CONTEXT_LENGTH,
                tool_calls=tool_calls, evidence=evidence, elapsed_ms=elapsed, tool_errors=tool_errors,
            )

        content = raw
        truncated = False
        # 截断到最大长度
        if len(content) > MAX_CONTEXT_LENGTH:
            content = content[:MAX_CONTEXT_LENGTH]
            truncated = True
            logger.info("search_context_truncated", original_length=len(raw))

        logger.info("search_context_fetched", length=len(content))
        return ContextResult(
            source="web",
            status="ok",
            summary=content,
            raw_summary_length=len(raw),
            truncated=truncated,
            max_length=MAX_CONTEXT_LENGTH,
            tool_calls=tool_calls,
            evidence=evidence,
            elapsed_ms=elapsed,
            tool_errors=tool_errors,
        )

    except asyncio.TimeoutError:
        elapsed = round((time.monotonic() - t1) * 1000)
        logger.warning("search_agent_timeout", timeout=AGENT_TIMEOUT_SECONDS, elapsed_ms=elapsed, user_input=user_input[:100])
        return ContextResult(source="web", status="timeout", max_length=MAX_CONTEXT_LENGTH, elapsed_ms=elapsed)
    except Exception as e:
        logger.warning("search_agent_error", error=str(e), error_type=type(e).__name__, user_input=user_input[:100])
        return ContextResult(
            source="web", status="error", max_length=MAX_CONTEXT_LENGTH,
            elapsed_ms=round((time.monotonic() - t1) * 1000), error=f"{type(e).__name__}: {e}"[:500],
        )
