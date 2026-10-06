"""资料更新接口的头像地址校验测试"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import create_token
from app.main import app


@pytest.fixture
def auth_header():
    token = create_token(user_id=1, openid="test_openid_123")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
class TestUpdateProfileAvatarValidation:
    async def test_rejects_device_temp_path(self, auth_header):
        with patch(
            "app.services.user_service.user_repository.update_user_profile",
            AsyncMock(),
        ) as update:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put(
                    "/api/v1/user/profile",
                    json={"avatar_url": "http://tmp/UrFzWNNoC6PW069376a9de4e3200a12856040c55c79f.jpeg"},
                    headers=auth_header,
                )

        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == 4002
        update.assert_not_awaited()

    async def test_rejects_wxfile_path(self, auth_header):
        with patch("app.services.user_service.user_repository.update_user_profile", AsyncMock()) as update:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put(
                    "/api/v1/user/profile",
                    json={"avatar_url": "wxfile://tmp_abcdef.jpeg"},
                    headers=auth_header,
                )
        assert resp.status_code == 400
        update.assert_not_awaited()

    async def test_accepts_persistable_https_url(self, auth_header):
        with patch(
            "app.services.user_service.user_repository.update_user_profile",
            AsyncMock(),
        ) as update:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put(
                    "/api/v1/user/profile",
                    json={"avatar_url": "https://cos.example.com/avatars/1/a.png"},
                    headers=auth_header,
                )

        assert resp.status_code == 200
        assert resp.json()["code"] == 0
        update.assert_awaited_once_with(1, None, "https://cos.example.com/avatars/1/a.png")

    async def test_nickname_only_update_still_works(self, auth_header):
        with patch(
            "app.services.user_service.user_repository.update_user_profile",
            AsyncMock(),
        ) as update:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put(
                    "/api/v1/user/profile",
                    json={"nickname": "桃MOON同学"},
                    headers=auth_header,
                )

        assert resp.status_code == 200
        update.assert_awaited_once_with(1, "桃MOON同学", None)