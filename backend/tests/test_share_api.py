"""分享接口测试：海报小程序码 + 公开只读详情"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import ShareError
from app.main import app

PUBLIC_DETAIL = {
    "quiz_id": "quiz_abc123def456",
    "title": "家具装修知识",
    "summary": "围绕家具装修生成的题库",
    "questions": [{"id": "q1", "stem": "承重墙可以拆吗？"}],
    "answer_records": [{"question_id": "q1", "is_correct": True}],
    "report": {"accuracy": 40},
    "created_at": "2026-10-06 10:00:00",
}

# 模拟仓储层未来误把用户身份字段带出来，公开接口也必须过滤掉
LEAKY_DETAIL = {
    **PUBLIC_DETAIL,
    "user_id": 9,
    "nickname": "不该出现的昵称",
    "avatar_url": "https://cos.example.com/avatars/9/a.png",
    "openid": "openid_should_not_leak",
}


@pytest.mark.asyncio
class TestPosterQrcodeAPI:
    async def test_returns_qrcode_url(self):
        with patch(
            "app.api.v1.routes.share.quiz_repository.quiz_exists", AsyncMock(return_value=True)
        ), patch(
            "app.api.v1.routes.share.wechat_qrcode_service.generate_poster_qrcode",
            AsyncMock(return_value="https://cos.example.com/share-qrcodes/a.png"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/v1/share/poster-qrcode",
                    params={"quiz_id": "quiz_abc123def456"},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["qrcode_url"] == "https://cos.example.com/share-qrcodes/a.png"

    async def test_unknown_quiz_returns_business_error(self):
        with patch(
            "app.api.v1.routes.share.quiz_repository.quiz_exists", AsyncMock(return_value=False)
        ), patch(
            "app.api.v1.routes.share.wechat_qrcode_service.generate_poster_qrcode", AsyncMock()
        ) as generate:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/v1/share/poster-qrcode",
                    params={"quiz_id": "quiz_missing"},
                )

        assert resp.status_code == 200
        assert resp.json()["code"] == 4004
        generate.assert_not_awaited()

    async def test_qrcode_failure_returns_server_error(self):
        with patch(
            "app.api.v1.routes.share.quiz_repository.quiz_exists", AsyncMock(return_value=True)
        ), patch(
            "app.api.v1.routes.share.wechat_qrcode_service.generate_poster_qrcode",
            AsyncMock(side_effect=ShareError("小程序码生成失败，请稍后重试")),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/v1/share/poster-qrcode",
                    params={"quiz_id": "quiz_abc123def456"},
                )

        assert resp.status_code == 500
        assert resp.json()["code"] == 5003


@pytest.mark.asyncio
class TestSharedQuizDetailAPI:
    async def test_public_access_without_token(self):
        with patch(
            "app.api.v1.routes.share.quiz_repository.get_public_quiz_detail",
            AsyncMock(return_value=PUBLIC_DETAIL),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/share/quizzes/quiz_abc123def456")

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["title"] == "家具装修知识"

    async def test_response_contains_only_whitelisted_fields(self):
        with patch(
            "app.api.v1.routes.share.quiz_repository.get_public_quiz_detail",
            AsyncMock(return_value=LEAKY_DETAIL),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/share/quizzes/quiz_abc123def456")

        data = resp.json()["data"]
        assert set(data.keys()) == {
            "quiz_id",
            "title",
            "summary",
            "questions",
            "answer_records",
            "report",
            "created_at",
        }
        for leaked_field in ("user_id", "nickname", "avatar_url", "openid"):
            assert leaked_field not in data

    async def test_missing_quiz_returns_business_error(self):
        with patch(
            "app.api.v1.routes.share.quiz_repository.get_public_quiz_detail",
            AsyncMock(return_value=None),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/share/quizzes/quiz_missing")

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 4004
        assert "报告不存在" in body["message"]

    async def test_private_profile_route_still_requires_auth(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/user/profile")
        assert resp.status_code == 401