"""微信小程序码服务：为分享海报生成小程序码并持久化到对象存储。"""

from __future__ import annotations

import time
import uuid

import httpx
import structlog

from app.core.config import get_settings
from app.core.exceptions import ShareError
from app.services import cos_service

logger = structlog.get_logger()

ACCESS_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
UNLIMITED_QRCODE_URL = "https://api.weixin.qq.com/wxa/getwxacodeunlimit"
# access_token 提前过期的安全余量，避免并发使用时刚好过期
TOKEN_EXPIRE_SAFETY_SECONDS = 300
TOKEN_EXPIRED_ERROR_CODES = (40001, 42001)

_token_cache: dict[str, object] = {"access_token": "", "expires_at": 0.0}


def reset_token_cache() -> None:
    """清空 access_token 缓存（供测试与排障使用）。"""
    _token_cache["access_token"] = ""
    _token_cache["expires_at"] = 0.0


async def _request_access_token() -> str:
    settings = get_settings()
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise ShareError("微信小程序未配置，暂时无法生成小程序码")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                ACCESS_TOKEN_URL,
                params={
                    "grant_type": "client_credential",
                    "appid": settings.wechat_app_id,
                    "secret": settings.wechat_app_secret,
                },
            )
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.error("wechat_access_token_request_failed", error=str(exc), exc_info=True)
        raise ShareError("获取微信访问凭证失败，请稍后重试") from exc
    except ValueError as exc:
        logger.error("wechat_access_token_invalid_response", error=str(exc), exc_info=True)
        raise ShareError("获取微信访问凭证失败，请稍后重试") from exc

    token = data.get("access_token")
    if not token:
        logger.error(
            "wechat_access_token_failed",
            errcode=data.get("errcode"),
            errmsg=data.get("errmsg"),
        )
        raise ShareError("获取微信访问凭证失败，请稍后重试")

    expires_in = int(data.get("expires_in") or 7200)
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = time.time() + max(60, expires_in - TOKEN_EXPIRE_SAFETY_SECONDS)
    return token


async def get_access_token(force_refresh: bool = False) -> str:
    """获取 access_token，命中未过期缓存时直接复用。"""
    cached_token = str(_token_cache.get("access_token") or "")
    expires_at = float(_token_cache.get("expires_at") or 0.0)
    if not force_refresh and cached_token and time.time() < expires_at:
        return cached_token
    return await _request_access_token()


async def _call_qrcode_api(token: str, payload: dict) -> tuple[bytes | None, int | None]:
    """调用 getwxacodeunlimit，成功返回图片字节，失败返回微信错误码。"""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                UNLIMITED_QRCODE_URL,
                params={"access_token": token},
                json=payload,
            )
    except httpx.HTTPError as exc:
        logger.error("wechat_qrcode_request_failed", error=str(exc), exc_info=True)
        raise ShareError("小程序码生成失败，请稍后重试") from exc

    content = resp.content or b""
    content_type = (resp.headers.get("content-type") or "").lower()
    if content.lstrip().startswith(b"{") or "application/json" in content_type:
        errcode: int | None = None
        errmsg = ""
        try:
            detail = resp.json()
            errcode = detail.get("errcode")
            errmsg = detail.get("errmsg") or ""
        except ValueError:
            errmsg = content[:200].decode("utf-8", errors="ignore")
        logger.error("wechat_qrcode_api_error", errcode=errcode, errmsg=errmsg)
        return None, errcode

    if not content:
        logger.error("wechat_qrcode_empty_response", content_type=content_type)
        raise ShareError("小程序码生成失败，请稍后重试")
    return content, None


async def request_qrcode(scene: str, page: str | None = None, width: int | None = None) -> bytes:
    """请求小程序码图片字节；token 失效时自动刷新并重试一次。"""
    settings = get_settings()
    payload = {
        "scene": scene,
        "page": page or settings.share_qrcode_page,
        # 分享页尚未发布时也允许生成小程序码
        "check_path": False,
        "env_version": settings.wechat_miniprogram_env_version or "release",
        "width": width or settings.share_qrcode_width,
        "is_hyaline": False,
    }

    for attempt in (1, 2):
        token = await get_access_token(force_refresh=attempt == 2)
        content, errcode = await _call_qrcode_api(token, payload)
        if content is not None:
            return content
        if errcode in TOKEN_EXPIRED_ERROR_CODES and attempt == 1:
            logger.warning("wechat_qrcode_token_expired_retry", errcode=errcode)
            continue
        raise ShareError("小程序码生成失败，请稍后重试")

    raise ShareError("小程序码生成失败，请稍后重试")


def _detect_image_type(content: bytes) -> tuple[str, str]:
    """按文件头判断小程序码的实际格式（微信接口可能返回 JPEG 或 PNG）。"""
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    return "png", "image/png"


async def generate_poster_qrcode(quiz_id: str) -> str:
    """生成分享海报用的小程序码，上传对象存储后返回可公开访问的 URL。"""
    settings = get_settings()
    content = await request_qrcode(scene=quiz_id)
    extension, content_type = _detect_image_type(content)
    key = f"{settings.cos_upload_prefix}share-qrcodes/{quiz_id}/{uuid.uuid4().hex}.{extension}"
    return await cos_service.upload_image_bytes(content, key, content_type=content_type)