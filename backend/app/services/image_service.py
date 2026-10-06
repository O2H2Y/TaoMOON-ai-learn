"""题目配图生成服务

负责：
1. 根据题目内容构建生图 Prompt
2. 调用阿里云百炼 qwen-image 模型生成图片（多题并发 + 进程级匀速节流 + 限流重试）
3. 下载生成的图片并上传至腾讯云 COS，得到永久可访问 URL
4. 应用「每人每天生图次数」限额

关于接口：千问图像模型走**原生同步接口**
`POST {base}/api/v1/services/aigc/multimodal-generation/generation`（官方文档推荐，
compatible-mode 的 /images/generations 并不支持 qwen-image-2.0，会返回 400 url error）。

关于限流：百炼图像生成模型同时受 RPM 与秒级 RPS 约束，瞬时并发爆发即使未超 RPM
也会被拒（429 limit_requests / limit_burst_rate）。因此本模块在「请求启动」这一层
做进程级匀速调度，并对限流 / 5xx / 网络抖动做指数退避重试。

关于配图范围：是否配图由出题模型逐题判断（Question.need_image），只给真正需要
视觉辅助的题目生图，以节省成本。

生图失败（单张或全部）不应影响出题主流程，因此本模块的对外入口
`generate_images_for_quiz` 永远不会抛出异常。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Optional

import httpx
import requests
import structlog
from requests.exceptions import RequestException

from app.core.config import get_settings
from app.models.quiz import Question
from app.repositories import image_repository
from app.services.cos_service import upload_image_bytes

logger = structlog.get_logger()

IMAGE_DOWNLOAD_TIMEOUT = 30.0
IMAGE_GENERATION_TIMEOUT = 120.0
COMPATIBLE_MODE_SUFFIX = "/compatible-mode/v1"
NATIVE_MODE_SUFFIX = "/api/v1"

# 百炼限流类错误码（官方错误码文档 429-Throttling.*），命中即值得重试
RETRYABLE_PROVIDER_CODES = frozenset(
    {
        "limit_requests",
        "limit_burst_rate",
        "limit_queries",
        "throttling",
        "throttling.ratequota",
        "throttling.burstrate",
        "throttling.concurrency",
        "throttling.serviceoverloaded",
        "throttling.resourceexhausted",
        "too many requests",
    }
)


class ImageGenerationRetryableError(RuntimeError):
    """可重试的生图失败：限流（429）、服务端 5xx、网络抖动。

    `retry_after` 为响应头 Retry-After 的秒数（服务端返回时优先遵循）。
    """

    def __init__(self, message: str, *, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class _RequestPacer:
    """进程级匀速调度器：保证任意两次真实请求的启动间隔不小于 min_interval。

    百炼限流同时作用于 RPM 与秒级 RPS，瞬时并发爆发即使未超 RPM 也会被拒，
    官方建议「匀速调度 / 指数退避 / 请求队列缓冲」。生图额度是账号（业务空间）级的，
    所以节流器必须跨题目、跨任务、跨用户共享，故使用模块级单例 `_pacer`。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_start: Optional[float] = None

    async def wait_turn(self, min_interval: float) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._last_start is not None and min_interval > 0:
                wait_seconds = self._last_start + min_interval - now
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
                    now = time.monotonic()
            self._last_start = now


_pacer = _RequestPacer()


def _get_image_generation_url(settings) -> str:
    """解析百炼「千问图像模型」原生同步接口地址。

    官方文档（华北2 北京）：
    POST https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/
         services/aigc/multimodal-generation/generation

    兼容模式地址（/compatible-mode/v1）与裸域名都会被归一化为 /api/v1 根地址。
    """
    base_url = _derive_image_base_url(
        (settings.dashscope_image_base_url or settings.dashscope_base_url).rstrip("/")
    )
    if not base_url.endswith(NATIVE_MODE_SUFFIX):
        base_url = f"{base_url}{NATIVE_MODE_SUFFIX}"
    return f"{base_url}/services/aigc/multimodal-generation/generation"


def _parse_provider_error(response, response_json: object) -> tuple[str, str, str]:
    payload = response_json if isinstance(response_json, dict) else {}
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = str(error.get("code") or payload.get("code") or response.status_code)
    message = str(error.get("message") or payload.get("message") or "")
    request_id = str(
        payload.get("request_id")
        or response.headers.get("x-dashscope-request-id", "")
        or ""
    )
    return code, message, request_id


def _parse_retry_after(response) -> Optional[float]:
    """解析 Retry-After 响应头（仅支持秒数形式；HTTP 日期形式忽略）。"""
    raw = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _is_retryable_error(status_code: int, provider_code: str) -> bool:
    """是否值得重试：429、服务端 5xx，或命中限流错误码。"""
    if status_code == 429:
        return True
    if 500 <= status_code < 600:
        return True
    return provider_code.strip().lower() in RETRYABLE_PROVIDER_CODES


def _derive_image_base_url(dashscope_base_url: str) -> str:
    """从 OpenAI 兼容模式的 dashscope_base_url 派生原生 DashScope API 地址。

    例如 https://xxx.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
    -> https://xxx.cn-beijing.maas.aliyuncs.com/api/v1
    """
    if dashscope_base_url.endswith("/compatible-mode/v1"):
        return dashscope_base_url[: -len("/compatible-mode/v1")] + "/api/v1"
    return dashscope_base_url


def build_image_prompt(question: Question) -> str:
    """根据题目内容构建生图 Prompt。"""
    return (
        f"为一道学习闯关题目生成配图。知识点：{question.knowledge_point}。"
        f"题目内容：{question.stem}。\n"
        "要求：画面聚焦题目描述的核心主体（如具体的单词实物、动植物、历史场景、地理风貌等），"
        "根据内容自行选择最合适的写实摄影或扁平插画风格，构图简洁、主体突出、色彩明快，"
        "适合作为教学配图；画面中不要出现任何文字、字母、水印或题目答案。"
    )


def _call_image_model_sync(prompt: str) -> str:
    """单次调用百炼原生同步生图接口，成功时返回图片临时 URL。

    阻塞调用，必须放在线程中执行。失败时：可重试的限流 / 5xx / 网络错误抛
    `ImageGenerationRetryableError`，其余业务错误抛 `RuntimeError`；
    匀速节流与重试由上层 `_call_image_model_with_retry` 负责。
    """
    settings = get_settings()
    api_url = _get_image_generation_url(settings)
    api_key = settings.dashscope_image_api_key or settings.dashscope_api_key
    payload = {
        "model": settings.dashscope_image_model,
        "input": {
            "messages": [{"role": "user", "content": [{"text": prompt}]}]
        },
        "parameters": {
            "size": settings.image_gen_size,
            "n": 1,
            "prompt_extend": True,
            "watermark": False,
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            api_url,
            json=payload,
            headers=headers,
            timeout=IMAGE_GENERATION_TIMEOUT,
        )
    except RequestException as exc:
        raise ImageGenerationRetryableError(
            f"image generation network error: {exc}"
        ) from exc

    try:
        response_json = response.json()
    except ValueError:
        response_json = None

    if response.status_code != 200:
        provider_code, provider_message, request_id = _parse_provider_error(
            response, response_json
        )
        logger.error(
            "dashscope_image_generation_failed",
            http_status=response.status_code,
            provider_code=provider_code,
            provider_message=provider_message,
            request_id=request_id,
            model=settings.dashscope_image_model,
            endpoint=api_url,
        )
        message = f"image generation failed: {provider_code} {provider_message}"
        if _is_retryable_error(response.status_code, provider_code):
            raise ImageGenerationRetryableError(
                message, retry_after=_parse_retry_after(response)
            )
        raise RuntimeError(message)

    if response_json is None:
        raise RuntimeError(
            f"image generation returned non-JSON response: HTTP {response.status_code}"
        )

    return _extract_image_url(response_json)


def _extract_image_url(payload: object) -> str:
    """从原生接口响应中取出图片 URL。

    结构：output.choices[0].message.content[*].image
    """
    content: object = None
    try:
        content = payload["output"]["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            "image generation succeeded but returned no image URL"
        ) from exc

    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("image"):
                return str(item["image"])

    raise RuntimeError("image generation succeeded but returned no image URL")


def _select_questions_for_images(questions: list[Question], quiz_id: str) -> list[Question]:
    """按 AI 的逐题判断挑选出需要配图的题目，以节省生图成本。

    - 有题目被明确标记 `need_image=True` → 只为这些题生图
    - 所有题目都被明确判断为 False → 本次完全不生图（AI 判断无需视觉辅助）
    - 一道题都没被判断（字段缺失，如模型未按新 Prompt 输出）→ 回退为全部生图，
      避免 Prompt 异常导致配图功能静默失效
    """
    flagged = [q for q in questions if getattr(q, "need_image", None) is True]
    if flagged:
        logger.info(
            "image_questions_selected_by_ai",
            quiz_id=quiz_id,
            total=len(questions),
            selected=len(flagged),
        )
        return flagged

    if any(getattr(q, "need_image", None) is not None for q in questions):
        logger.info("image_generation_skipped_by_ai", quiz_id=quiz_id, total=len(questions))
        return []

    logger.warning("image_need_not_judged", quiz_id=quiz_id, total=len(questions))
    return list(questions)


async def _call_image_model_with_retry(prompt: str) -> str:
    """进程级匀速节流 + 指数退避重试的生图调用（仅重试限流 / 5xx / 网络错误）。"""
    settings = get_settings()
    max_attempts = max(1, settings.image_gen_max_attempts)
    base_delay = max(0.0, settings.image_gen_retry_base_delay_seconds)
    max_delay = max(base_delay, settings.image_gen_retry_max_delay_seconds)

    for attempt in range(1, max_attempts + 1):
        await _pacer.wait_turn(settings.image_gen_min_interval_seconds)
        try:
            return await asyncio.to_thread(_call_image_model_sync, prompt)
        except ImageGenerationRetryableError as exc:
            if attempt >= max_attempts:
                raise
            delay = exc.retry_after if exc.retry_after else base_delay * 2 ** (attempt - 1)
            delay = min(max(delay, 0.0), max_delay)
            logger.warning(
                "image_generation_retry_scheduled",
                attempt=attempt,
                max_attempts=max_attempts,
                wait_seconds=round(delay, 2),
                error=str(exc),
            )
            await asyncio.sleep(delay)

    raise RuntimeError("image generation failed after retries")  # pragma: no cover


async def _download_image(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=IMAGE_DOWNLOAD_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def generate_image_for_question(question: Question, quiz_id: str) -> Optional[str]:
    """为单道题目生成配图并上传至 COS，返回永久 URL；任何失败都返回 None（不抛异常）。"""
    try:
        prompt = build_image_prompt(question)
        temp_url = await _call_image_model_with_retry(prompt)
        image_bytes = await _download_image(temp_url)

        settings = get_settings()
        key = f"{settings.cos_upload_prefix}{quiz_id}/{question.id}{uuid.uuid4().hex[:8]}.png"
        return await upload_image_bytes(image_bytes, key)
    except Exception as e:
        logger.warning(
            "question_image_generation_failed",
            question_id=question.id,
            quiz_id=quiz_id,
            error=str(e),
        )
        return None


async def generate_images_for_quiz(
    questions: list[Question],
    user_id: Optional[int],
    quiz_id: str,
) -> tuple[dict[str, str], Optional[str]]:
    """为一批题目并发生成配图，返回 (question_id -> image_url 映射, 提示信息)。

    该函数不会抛出异常：任何内部错误都会被吞掉并转化为提示信息或直接忽略配图。
    """
    settings = get_settings()

    if user_id is None:
        return {}, "登录后才能使用生成图片功能，本次出题未生成配图"

    try:
        used = await image_repository.get_today_usage_count(user_id)
    except Exception as e:
        logger.warning("image_quota_check_failed", user_id=user_id, error=str(e))
        used = 0

    remaining = max(0, settings.image_gen_daily_limit - used)

    if remaining <= 0:
        return {}, f"今日生成图片次数已用完（{settings.image_gen_daily_limit} 张/天），本次出题未生成配图"

    selected_questions = _select_questions_for_images(questions, quiz_id)
    if not selected_questions:
        return {}, None

    target_questions = selected_questions[:remaining]
    notice = None
    if len(target_questions) < len(selected_questions):
        notice = (
            f"今日生图额度剩余 {remaining} 张，已为前 {len(target_questions)} 题生成配图，"
            "其余题目未配图"
        )

    semaphore = asyncio.Semaphore(settings.image_gen_max_concurrency)

    async def _run(q: Question) -> tuple[str, Optional[str]]:
        async with semaphore:
            url = await generate_image_for_question(q, quiz_id)
            return q.id, url

    results = await asyncio.gather(*(_run(q) for q in target_questions))

    image_map: dict[str, str] = {}
    for question_id, url in results:
        if not url:
            continue
        image_map[question_id] = url
        try:
            await image_repository.log_image_generation(user_id, quiz_id, question_id, url)
        except Exception as e:
            logger.warning("image_usage_log_failed", user_id=user_id, error=str(e))

    logger.info(
        "image_batch_summary",
        quiz_id=quiz_id,
        user_id=user_id,
        requested=len(target_questions),
        succeeded=len(image_map),
        failed=len(target_questions) - len(image_map),
    )

    return image_map, notice
