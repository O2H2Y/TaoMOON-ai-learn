"""头像上传接口测试"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import create_token
from app.main import app

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture
def auth_header():
    token = create_token(user_id=1, openid="test_openid_123")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
class TestAvatarUploadAPI:
    async def test_upload_success(self, auth_header):
        with patch(
            "app.services.avatar_service.cos_service.upload_image_bytes",
            AsyncMock(return_value="https://cos.example.com/avatars/1/a.png"),
        ), patch(
            "app.services.avatar_service.user_repository.update_user_profile",
            AsyncMock(),
        ) as update:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/user/avatar",
                    files={"file": ("avatar.png", PNG_BYTES, "image/png")},
                    headers=auth_header,
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["avatar_url"] == "https://cos.example.com/avatars/1/a.png"
        update.assert_awaited_once_with(1, None, "https://cos.example.com/avatars/1/a.png")

    async def test_upload_rejects_non_image(self, auth_header):
        with patch(
            "app.services.avatar_service.cos_service.upload_image_bytes",
            AsyncMock(),
        ) as upload:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/user/avatar",
                    files={"file": ("a.txt", b"hello", "text/plain")},
                    headers=auth_header,
                )

        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == 4002
        upload.assert_not_awaited()

    async def test_upload_rejects_oversized_file(self, auth_header):
        from app.core.config import get_settings

        max_bytes = max(1, get_settings().avatar_max_size_mb) * 1024 * 1024
        with patch(
            "app.services.avatar_service.cos_service.upload_image_bytes",
            AsyncMock(),
        ) as upload:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/user/avatar",
                    files={"file": ("big.png", PNG_BYTES + b"0" * max_bytes, "image/png")},
                    headers=auth_header,
                )

        assert resp.status_code == 400
        assert resp.json()["code"] == 4002
        upload.assert_not_awaited()

    async def test_upload_requires_auth(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/user/avatar",
                files={"file": ("avatar.png", PNG_BYTES, "image/png")},
            )
        assert resp.status_code == 401