"""LangChain ChatOpenAI 工厂"""

from functools import lru_cache
from typing import Any

from langchain_openai import ChatOpenAI

from app.core.config import get_settings


def deepseek_extra_body() -> dict[str, Any]:
    """DeepSeek 专有参数：思考模式开关（OpenAI 格式下需放在 extra_body 里）。"""
    return {"thinking": {"type": get_settings().deepseek_thinking}}


@lru_cache()
def get_chat_model(temperature: float = 0.4) -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        temperature=temperature,
        max_tokens=4096,
        extra_body=deepseek_extra_body(),
    )
