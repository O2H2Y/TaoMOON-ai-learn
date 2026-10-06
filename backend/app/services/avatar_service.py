"""用户头像服务：校验并上传头像文件，保证入库与渲染的都是可持久化地址。"""

from __future__ import annotations

import uuid

import structlog

from app.core.config import get_settings
from app.core.exceptions import AvatarError
from app.repositories import user_repository
from app.services import cos_service

logger = structlog.get_logger()

# 设备临时文件路径前缀：这些地址只在当前设备 / 会话内有效，禁止入库，也禁止渲染
TEMP_URL_PREFIXES = (
    "http://tmp/",
    "https://tmp/",
    "wxfile://",
    "weixin://",
    "http://127.0.0.1",
    "http://localhost",
)

_ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

_MAGIC_SIGNATURES = (
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
)


def is_temp_avatar_url(url: str | None) -> bool:
    """判断头像地址是否为设备临时路径（这类地址换设备/清缓存后必然失效）。"""
    if not url:
        return False
    return url.strip().lower().startswith(TEMP_URL_PREFIXES)


def validate_avatar_url(url: str | None) -> None:
    """校验客户端提交的头像地址必须是可持久化地址，否则抛出 AvatarError。"""
    if url is None:
        return
    stripped = url.strip()
    if not stripped:
        return
    if is_temp_avatar_url(stripped):
        raise AvatarError("头像地址不能使用设备临时路径，请重新选择头像")
    if not stripped.lower().startswith(("http://", "https://")):
        raise AvatarError("头像地址必须是可持久访问的 http(s) 链接")


def _detect_image(content: bytes, content_type: str) -> tuple[str, str] | None:
    """按文件头识别图片类型，识别失败时回退到声明的 Content-Type。"""
    for signature, extension, mime in _MAGIC_SIGNATURES:
        if content.startswith(signature):
            return extension, mime
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp", "image/webp"
    normalized = (content_type or "").split(";")[0].strip().lower()
    if normalized in _ALLOWED_CONTENT_TYPES:
        return _ALLOWED_CONTENT_TYPES[normalized], normalized
    return None


async def update_avatar(
    user_id: int,
    content: bytes,
    content_type: str = "",
    filename: str = "",
) -> str:
    """校验并上传头像，成功后把可持久化地址写入用户资料，返回该地址。"""
    settings = get_settings()
    if not content:
        raise AvatarError("头像文件为空，请重新选择头像")

    max_bytes = max(1, settings.avatar_max_size_mb) * 1024 * 1024
    if len(content) > max_bytes:
        raise AvatarError(f"头像文件不能超过 {settings.avatar_max_size_mb} MB，请压缩后重试")

    detected = _detect_image(content, content_type)
    if detected is None:
        raise AvatarError("仅支持 JPG / PNG / WebP 格式的图片")

    extension, mime = detected
    key = f"{settings.cos_upload_prefix}avatars/{user_id}/{uuid.uuid4().hex}.{extension}"
    url = await cos_service.upload_image_bytes(content, key, content_type=mime)
    await user_repository.update_user_profile(user_id, None, url)
    logger.info("avatar_updated", user_id=user_id, key=key, file_name=filename)
    return url