"""头像服务单元测试"""

from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import get_settings
from app.core.exceptions import AvatarError
from app.services import avatar_service

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"0" * 32


def test_is_temp_avatar_url_recognises_device_paths():
    assert avatar_service.is_temp_avatar_url("http://tmp/UrFzWNNo.jpeg") is True
    assert avatar_service.is_temp_avatar_url("https://tmp/UrFzWNNo.jpeg") is True
    assert avatar_service.is_temp_avatar_url("wxfile://tmp_abc.jpeg") is True
    assert avatar_service.is_temp_avatar_url("https://cos.example.com/avatars/a.png") is False
    assert avatar_service.is_temp_avatar_url("") is False
    assert avatar_service.is_temp_avatar_url(None) is False


def test_validate_avatar_url_rejects_temp_path():
    with pytest.raises(AvatarError):
        avatar_service.validate_avatar_url("http://tmp/UrFzWNNo.jpeg")


def test_validate_avatar_url_rejects_non_http_value():
    with pytest.raises(AvatarError):
        avatar_service.validate_avatar_url("/static/avatar.png")


def test_validate_avatar_url_accepts_persistable_url():
    avatar_service.validate_avatar_url("https://cos.example.com/avatars/a.png")
    avatar_service.validate_avatar_url(None)
    avatar_service.validate_avatar_url("")


@pytest.mark.asyncio
async def test_update_avatar_uploads_and_updates_profile():
    with patch.object(
        avatar_service.cos_service,
        "upload_image_bytes",
        AsyncMock(return_value="https://cos.example.com/avatars/a.png"),
    ) as upload, patch.object(
        avatar_service.user_repository,
        "update_user_profile",
        AsyncMock(),
    ) as update:
        url = await avatar_service.update_avatar(7, PNG_BYTES, "image/png", "a.png")

    assert url == "https://cos.example.com/avatars/a.png"
    upload.assert_awaited_once()
    key = upload.await_args.args[1]
    assert key.startswith(f"{get_settings().cos_upload_prefix}avatars/7/")
    assert key.endswith(".png")
    update.assert_awaited_once_with(7, None, "https://cos.example.com/avatars/a.png")


@pytest.mark.asyncio
async def test_update_avatar_detects_jpeg_by_magic_bytes():
    with patch.object(
        avatar_service.cos_service, "upload_image_bytes", AsyncMock(return_value="https://cos.example.com/a.jpg")
    ) as upload, patch.object(avatar_service.user_repository, "update_user_profile", AsyncMock()):
        await avatar_service.update_avatar(7, JPEG_BYTES, "", "a")

    assert upload.await_args.args[1].endswith(".jpg")


@pytest.mark.asyncio
async def test_update_avatar_rejects_non_image_without_uploading():
    with patch.object(avatar_service.cos_service, "upload_image_bytes", AsyncMock()) as upload:
        with pytest.raises(AvatarError):
            await avatar_service.update_avatar(7, b"not-an-image", "text/plain", "a.txt")
    upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_avatar_rejects_oversized_file():
    max_bytes = max(1, get_settings().avatar_max_size_mb) * 1024 * 1024
    oversized = PNG_BYTES + b"0" * max_bytes
    with patch.object(avatar_service.cos_service, "upload_image_bytes", AsyncMock()) as upload:
        with pytest.raises(AvatarError):
            await avatar_service.update_avatar(7, oversized, "image/png", "big.png")
    upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_avatar_rejects_empty_file():
    with pytest.raises(AvatarError):
        await avatar_service.update_avatar(7, b"", "image/png", "empty.png")