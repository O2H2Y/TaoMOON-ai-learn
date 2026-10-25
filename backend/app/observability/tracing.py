"""Langfuse tracing（可选）。

未配置 Langfuse key、未安装 langfuse 包或 enable_tracing=False 时，返回空 callbacks，业务不受影响。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import structlog

from app.core.config import get_settings

logger = structlog.get_logger()


@lru_cache
def _langfuse_ready() -> bool:
    settings = get_settings()
    if not settings.enable_tracing:
        return False
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return False
    try:
        from langfuse import Langfuse

        # 初始化全局客户端；CallbackHandler 会复用它
        Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            environment=settings.langfuse_environment or None,
        )
        return True
    except Exception as e:  # 包未安装或初始化失败
        logger.warning("langfuse_init_failed", error=str(e))
        return False


def trace_user_id(user_id: int | None) -> str:
    """链路记录里使用的用户标识：只用内部数字 ID，不写 openid、昵称等个人信息；未登录记为 anonymous。"""
    return f"u_{user_id}" if user_id is not None else "anonymous"


def flush_tracing() -> None:
    """把缓冲中的 trace 发出去。短进程（评测脚本）退出前、服务关闭时调用。"""
    if not _langfuse_ready():
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as e:
        logger.warning("langfuse_flush_failed", error=str(e))


def get_trace_callbacks() -> list[Any]:
    """返回需要注入到 LangChain/LangGraph 调用里的 callback 列表。"""
    if not _langfuse_ready():
        return []
    try:
        from langfuse.langchain import CallbackHandler

        return [CallbackHandler(public_key=get_settings().langfuse_public_key)]
    except Exception as e:
        logger.warning("langfuse_handler_failed", error=str(e))
        return []


def build_langchain_config(
    run_name: str,
    extra_callbacks: list[Any] | None = None,
    **base: Any,
) -> dict[str, Any]:
    """构建 LangChain invoke config：注入 tracing callback、run_name、tags、metadata。

    metadata 中的 langfuse_session_id 使用 RunRecord.run_id，便于在 Langfuse 中把一次出题的
    搜索 agent、出题、生图等多个 trace 聚合查看；该 run_id 也会写入 quiz_sessions.trace_id。
    """
    from app.observability.run_context import current_run
    from app.prompts.registry import PROMPT_VERSIONS

    config: dict[str, Any] = dict(base)
    callbacks = list(extra_callbacks or []) + get_trace_callbacks()
    if callbacks:
        config["callbacks"] = callbacks
    config["run_name"] = run_name

    record = current_run()
    metadata: dict[str, Any] = {"prompt_versions": dict(PROMPT_VERSIONS)}
    tags = [f"stage:{run_name}"]
    if record is not None:
        metadata["langfuse_session_id"] = record.run_id
        metadata["run_id"] = record.run_id
        user = record.metadata.get("user_id")
        if user:
            metadata["langfuse_user_id"] = user
        tags.append(f"kind:{record.kind}")
    config["metadata"] = metadata
    config["tags"] = tags
    return config
