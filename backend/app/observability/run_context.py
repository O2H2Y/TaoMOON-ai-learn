"""单次运行记录：基于 contextvars，在一次出题/报告请求内收集证据、token 用量与分阶段耗时。"""

from __future__ import annotations

import contextvars
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator, Optional

import structlog

logger = structlog.get_logger()

# 单条工具输出在记录里最多保留的字符数（防止内存/落盘膨胀；评测足够用）
MAX_EVIDENCE_CHARS = 20000


@dataclass
class ContextResult:
    """检索/联网阶段的完整结果：除了传给出题模型的摘要，还保留原始证据。"""

    source: str  # "web" | "rag"
    status: str  # "ok" | "empty" | "timeout" | "error" | "disabled"
    summary: str = ""  # 实际传给出题模型的（截断后）文本
    raw_summary_length: int = 0  # 截断前长度
    truncated: bool = False
    max_length: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # [{name, args}]
    evidence: list[dict[str, Any]] = field(default_factory=list)  # [{tool, content}]
    elapsed_ms: int = 0
    error: str = ""
    tool_errors: int = 0  # 工具调用报错的次数（401、代理错误、参数错误等）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LLMUsage:
    stage: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 1


@dataclass
class RunRecord:
    run_id: str
    kind: str  # "quiz" | "report"
    metadata: dict[str, Any] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)  # [{name, elapsed_ms, ok}]
    usage: list[LLMUsage] = field(default_factory=list)
    contexts: list[ContextResult] = field(default_factory=list)
    raw_outputs: dict[str, str] = field(default_factory=dict)
    retrievals: list[dict[str, Any]] = field(default_factory=list)  # 向量检索明细：[{query, k, results}]
    started_at: float = field(default_factory=time.time)

    # ---- 汇总 ----
    def total_tokens(self) -> dict[str, int]:
        return {
            "input_tokens": sum(u.input_tokens for u in self.usage),
            "output_tokens": sum(u.output_tokens for u in self.usage),
            "total_tokens": sum(u.total_tokens for u in self.usage),
            "llm_calls": sum(u.calls for u in self.usage),
        }

    def stage_ms(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.stages:
            out[s["name"]] = out.get(s["name"], 0) + int(s["elapsed_ms"])
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            **self.metadata,
            "stage_ms": self.stage_ms(),
            **self.total_tokens(),
            "context_status": [c.status for c in self.contexts],
            "context_truncated": any(c.truncated for c in self.contexts),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "metadata": self.metadata,
            "stages": self.stages,
            "usage": [asdict(u) for u in self.usage],
            "contexts": [c.to_dict() for c in self.contexts],
            "raw_outputs": self.raw_outputs,
            "retrievals": self.retrievals,
            "started_at": self.started_at,
        }


_current: contextvars.ContextVar[Optional[RunRecord]] = contextvars.ContextVar(
    "taomoon_run_record", default=None
)


def current_run() -> Optional[RunRecord]:
    return _current.get()


@contextmanager
def start_run(kind: str, **metadata: Any) -> Iterator[RunRecord]:
    """开启一次运行记录。若外层已有记录（例如评测 runner 已开启），复用外层记录。"""
    existing = _current.get()
    if existing is not None:
        existing.metadata.update({k: v for k, v in metadata.items() if k not in existing.metadata})
        yield existing
        return

    record = RunRecord(run_id=f"run_{uuid.uuid4().hex[:16]}", kind=kind, metadata=dict(metadata))
    token = _current.set(record)
    try:
        yield record
    finally:
        _current.reset(token)
        try:
            logger.info("run_summary", **record.summary())
        except Exception:  # 记录失败绝不影响业务
            pass


@contextmanager
def stage(name: str) -> Iterator[None]:
    """记录一个阶段的耗时；无激活记录时仅计时不保存。"""
    t0 = time.monotonic()
    ok = True
    try:
        yield
    except BaseException:
        ok = False
        raise
    finally:
        record = _current.get()
        if record is not None:
            record.stages.append(
                {"name": name, "elapsed_ms": round((time.monotonic() - t0) * 1000), "ok": ok}
            )


def _usage_from_message(message: Any) -> tuple[int, int, int]:
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, dict):
        return 0, 0, 0
    inp = usage.get("input_tokens") or 0
    out = usage.get("output_tokens") or 0
    tot = usage.get("total_tokens") or (inp + out)
    if not all(isinstance(x, int) for x in (inp, out, tot)):
        return 0, 0, 0
    return inp, out, tot


def record_llm_usage(stage_name: str, model: str, messages: list[Any] | Any) -> None:
    """从 AIMessage（或消息列表）的 usage_metadata 累加 token 用量。"""
    record = _current.get()
    if record is None:
        return
    if not isinstance(messages, list):
        messages = [messages]
    inp = out = tot = calls = 0
    for m in messages:
        i, o, t = _usage_from_message(m)
        if i or o or t:
            calls += 1
        inp, out, tot = inp + i, out + o, tot + t
    if calls == 0:
        return
    record.usage.append(
        LLMUsage(stage=stage_name, model=str(model), input_tokens=inp, output_tokens=out, total_tokens=tot, calls=calls)
    )


def record_context(ctx: ContextResult) -> None:
    record = _current.get()
    if record is not None:
        record.contexts.append(ctx)


def record_retrieval(query: str, k: int | None, docs: list[Any], **extra: Any) -> None:
    """记录一次向量检索的 query 与命中的 chunk（id、元数据、内容），用于 recall@k / MRR。"""
    record = _current.get()
    if record is None:
        return
    results = []
    for rank, d in enumerate(docs or [], start=1):
        meta = getattr(d, "metadata", None)
        results.append(
            {
                "rank": rank,
                "id": getattr(d, "id", None),
                "metadata": dict(meta) if isinstance(meta, dict) else {},
                "content": str(getattr(d, "page_content", ""))[:MAX_EVIDENCE_CHARS],
            }
        )
    record.retrievals.append({"query": query, "k": k, "results": results, **extra})


def record_raw_output(name: str, text: str) -> None:
    record = _current.get()
    if record is not None and isinstance(text, str):
        record.raw_outputs[name] = text


def is_tool_error(content: Any, status: Any = None) -> bool:
    """判断一条工具输出是否是报错，而不是真实资料。

    兼容两种形态：langchain_tavily 捕获异常后返回 {"error": ...}（序列化成 "{'error': ...}"），
    以及 langgraph ToolNode 捕获异常后 status="error" / 内容以 "Error" 开头。
    """
    if status == "error":
        return True
    if isinstance(content, dict):
        return "error" in content and len(content) <= 2
    text = str(content or "").lstrip()
    return text.startswith("{'error'") or text.startswith('{"error"') or text.startswith("Error:")


def extract_agent_trace(messages: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """从 ReAct agent 的消息链路中抽取工具调用与原始工具输出（证据）。"""
    try:
        from langchain_core.messages import AIMessage, ToolMessage
    except Exception:  # pragma: no cover
        return [], []

    tool_calls: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for m in messages or []:
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                tool_calls.append({"name": tc.get("name"), "args": tc.get("args")})
        elif isinstance(m, ToolMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            evidence.append(
                {
                    "tool": getattr(m, "name", None),
                    "content": content[:MAX_EVIDENCE_CHARS],
                    "content_length": len(content),
                    "is_error": is_tool_error(content, getattr(m, "status", None)),
                }
            )
    return tool_calls, evidence
