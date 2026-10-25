"""可观测性：单次出题运行记录（RunRecord）与 Langfuse tracing。

设计原则：
- 不改变任何业务函数签名与行为；没有激活的 RunRecord 时所有记录函数均为 no-op。
- 离线评测（evals/）与线上请求都通过 start_run() 开启记录，拿到证据、用量、分阶段耗时。
"""

from app.observability.run_context import (  # noqa: F401
    ContextResult,
    RunRecord,
    current_run,
    record_context,
    record_llm_usage,
    record_raw_output,
    record_retrieval,
    stage,
    start_run,
)
from app.observability.tracing import build_langchain_config, get_trace_callbacks  # noqa: F401
