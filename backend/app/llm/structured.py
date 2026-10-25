"""结构化输出 + 校验失败自动重试。

用 LangChain 的 with_structured_output（DeepSeek 走 function calling）让模型直接按 Pydantic schema 输出，
解析或业务校验失败时自动重试；全部失败才抛错。每次尝试的 token 用量与原始输出都会记入 RunRecord。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from app.observability import build_langchain_config, current_run, record_llm_usage, record_raw_output

logger = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)

# 业务校验函数：返回问题列表，空列表表示通过
Validator = Callable[[Any], list[str]]


class StructuredOutputError(ValueError):
    """多次尝试后仍拿不到合格的结构化输出。"""


def _extract_json(text: str) -> dict:
    """兜底：模型没走工具调用、直接在正文里写了 JSON 时，从正文提取（兼容 markdown 代码块）。"""
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    raw = match.group(1).strip() if match else text.strip()
    return json.loads(raw)


def _raw_text(raw: Any) -> str:
    """把模型原始回复整理成可记录的文本：优先工具调用参数，其次正文。"""
    if raw is None:
        return ""
    tool_calls = getattr(raw, "tool_calls", None) or []
    if tool_calls:
        try:
            return json.dumps(tool_calls[0].get("args", {}), ensure_ascii=False)
        except Exception:
            pass
    content = getattr(raw, "content", "")
    return content if isinstance(content, str) else ""


def _parse(out: Any, schema: type[T]) -> tuple[Optional[T], str]:
    """从 include_raw=True 的输出里取解析结果；失败时尝试从正文兜底解析。返回 (结果, 错误说明)。"""
    if not isinstance(out, dict):
        return None, f"unexpected output type {type(out).__name__}"
    parsed = out.get("parsed")
    err = out.get("parsing_error")
    if parsed is not None and err is None:
        if isinstance(parsed, schema):
            return parsed, ""
        try:
            return schema.model_validate(parsed), ""
        except ValidationError as e:
            return None, f"schema: {e.error_count()} errors"
    content = getattr(out.get("raw"), "content", "")
    if isinstance(content, str) and content.strip():
        try:
            return schema.model_validate(_extract_json(content)), ""
        except (json.JSONDecodeError, ValidationError) as e:
            return None, f"fallback parse: {type(e).__name__}"
    return None, f"parse: {type(err).__name__ if err else 'no tool call'}"


async def invoke_structured(
    prompt: Any,
    llm: Any,
    schema: type[T],
    inputs: dict[str, Any],
    stage_name: str,
    validator: Optional[Validator] = None,
    max_attempts: int = 2,
) -> T:
    """调用模型拿结构化结果；解析失败或 validator 报问题时重试，最多 max_attempts 次。"""
    structured = llm.with_structured_output(schema, method="function_calling", include_raw=True)
    chain = prompt | structured
    model_name = getattr(llm, "model_name", "") or ""
    problems: list[str] = []

    for attempt in range(1, max_attempts + 1):
        run_name = stage_name if attempt == 1 else f"{stage_name}_retry{attempt - 1}"
        out = await chain.ainvoke(inputs, config=build_langchain_config(run_name))
        raw = out.get("raw") if isinstance(out, dict) else None
        if raw is not None:
            record_llm_usage(stage_name, model_name, raw)
        record_raw_output(stage_name if attempt == 1 else run_name, _raw_text(raw))

        result, err = _parse(out, schema)
        problems = [err] if err else (validator(result) if validator else [])
        record = current_run()
        if record is not None:
            record.metadata[f"{stage_name}_attempts"] = attempt
            record.metadata["output_mode"] = "function_calling"
        if not problems:
            return result  # type: ignore[return-value]
        logger.warning("structured_output_invalid", stage=stage_name, attempt=attempt, problems=problems[:5])

    record = current_run()
    if record is not None:
        record.metadata[f"{stage_name}_failed"] = problems[:5]
    raise StructuredOutputError(f"{stage_name} 结构化输出 {max_attempts} 次均不合格: {problems[:3]}")
