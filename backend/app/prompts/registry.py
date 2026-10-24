"""Prompt 版本登记表。

- 每个 prompt 有一个人类可读的版本号（写入 quiz_sessions.prompt_version、Langfuse metadata、评测报告）。
- PROMPT_FINGERPRINTS 记录每个版本对应的内容指纹；tests/test_prompt_registry.py 会校验
  当前 prompt 文本的指纹与登记值一致 —— 只要改了 prompt 文本却没有升版本号，单测就会失败，
  保证线上数据与评测结果永远能按 prompt 版本对比。

改 prompt 的流程：改文本 → 升 PROMPT_VERSIONS 中对应版本号 → 运行
`python -m app.prompts.registry` 打印新指纹 → 更新 PROMPT_FINGERPRINTS。
"""

from __future__ import annotations

import hashlib

from app.prompts.quiz_prompt import QUIZ_HUMAN_PROMPT, QUIZ_SYSTEM_PROMPT, SEARCH_CONTEXT_TEMPLATE
from app.prompts.rag_prompt import RAG_AGENT_SYSTEM_PROMPT
from app.prompts.report_prompt import REPORT_HUMAN_PROMPT, REPORT_SYSTEM_PROMPT
from app.prompts.search_prompt import SEARCH_AGENT_SYSTEM_PROMPT

PROMPT_VERSIONS: dict[str, str] = {
    "quiz": "quiz-v1",
    "report": "report-v1",
    "search_agent": "search-v1",
    "rag_agent": "rag-v1",
}

_PROMPT_TEXTS: dict[str, tuple[str, ...]] = {
    "quiz": (QUIZ_SYSTEM_PROMPT, QUIZ_HUMAN_PROMPT, SEARCH_CONTEXT_TEMPLATE),
    "report": (REPORT_SYSTEM_PROMPT, REPORT_HUMAN_PROMPT),
    "search_agent": (SEARCH_AGENT_SYSTEM_PROMPT,),
    "rag_agent": (RAG_AGENT_SYSTEM_PROMPT,),
}

PROMPT_FINGERPRINTS: dict[str, str] = {
    "quiz": "0f4e23b34105",
    "report": "99ad33e54e4b",
    "search_agent": "538e2259fd28",
    "rag_agent": "6c3846b6951a",
}


def fingerprint(name: str) -> str:
    joined = "\n\x1e\n".join(_PROMPT_TEXTS[name])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def current_fingerprints() -> dict[str, str]:
    return {name: fingerprint(name) for name in _PROMPT_TEXTS}


def prompt_version_tag() -> str:
    """写入 quiz_sessions.prompt_version 的紧凑标签，例如 quiz-v1+search-v1。"""
    return "+".join(PROMPT_VERSIONS[k] for k in ("quiz", "search_agent", "rag_agent"))


if __name__ == "__main__":  # pragma: no cover
    for k, v in current_fingerprints().items():
        print(f"{k}: {PROMPT_VERSIONS[k]} {v}")
