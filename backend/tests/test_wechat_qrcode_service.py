"""微信小程序码服务测试"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.exceptions import ShareError
from app.services import wechat_qrcode_service

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 16
TOKEN_PAYLOAD = {"access_token": "token-1", "expires_in": 7200}
TOKEN_EXPIRED_PAYLOAD = {"errcode": 40001, "errmsg": "invalid credential"}


class _FakeResponse:
    def __init__(self, content: bytes = b"", headers: dict | None = None, json_data=None):
        self.content = content
        self.headers = headers or {}
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            raise ValueError("response is not json")
        return self._json_data


def _fake_client(get_response=None, post_response=None, post_side_effect=None) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=get_response)
    if post_side_effect is not None:
        client.post = AsyncMock(side_effect=post_side_effect)
    else:
        client.post = AsyncMock(return_value=post_response)
    return client


def _patch_async_client(*clients) -> MagicMock:
    """按顺序返回预设的假客户端；用尽后复用最后一个，避免试探性调用抛 StopIteration。"""
    queue = list(clients)

    def _factory(*args, **kwargs):
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]

    return patch.object(wechat_qrcode_service.httpx, "AsyncClient", MagicMock(side_effect=_factory))


@pytest.fixture(autouse=True)
def _wechat_configured(monkeypatch):
    """这些用例假设已配置小程序 appid/secret；不依赖本地 .env，保证在 CI 与干净环境中可复现。"""
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "wechat_app_id", "wx-test-appid")
    monkeypatch.setattr(settings, "wechat_app_secret", "wx-test-secret")
    yield


@pytest.fixture(autouse=True)
def _clear_token_cache():
    wechat_qrcode_service.reset_token_cache()
    yield
    wechat_qrcode_service.reset_token_cache()


@pytest.mark.asyncio
async def test_access_token_is_cached_between_calls():
    client = _fake_client(get_response=_FakeResponse(json_data=TOKEN_PAYLOAD))
    with _patch_async_client(client):
        first = await wechat_qrcode_service.get_access_token()
        second = await wechat_qrcode_service.get_access_token()

    assert first == second == "token-1"
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_request_qrcode_returns_image_bytes():
    token_client = _fake_client(get_response=_FakeResponse(json_data=TOKEN_PAYLOAD))
    qrcode_client = _fake_client(
        post_response=_FakeResponse(content=PNG_BYTES, headers={"content-type": "image/png"})
    )
    with _patch_async_client(token_client, qrcode_client):
        content = await wechat_qrcode_service.request_qrcode("quiz_abc123def456")

    assert content == PNG_BYTES
    payload = qrcode_client.post.await_args.kwargs["json"]
    assert payload["scene"] == "quiz_abc123def456"
    assert payload["page"] == "pages/share/index"
    assert payload["check_path"] is False


@pytest.mark.asyncio
async def test_request_qrcode_refreshes_token_and_retries_once():
    token_client = _fake_client(get_response=_FakeResponse(json_data=TOKEN_PAYLOAD))
    qrcode_client = _fake_client(
        post_side_effect=[
            _FakeResponse(
                content=b'{"errcode":40001,"errmsg":"invalid credential"}',
                headers={"content-type": "application/json"},
                json_data=TOKEN_EXPIRED_PAYLOAD,
            ),
            _FakeResponse(content=PNG_BYTES, headers={"content-type": "image/png"}),
        ]
    )
    # 第一次 token -> 小程序码接口返回 40001 -> 强制刷新 token（第三次建连）-> 重试成功
    with _patch_async_client(token_client, qrcode_client, token_client, qrcode_client):
        content = await wechat_qrcode_service.request_qrcode("quiz_abc123def456")

    assert content == PNG_BYTES
    assert qrcode_client.post.await_count == 2
    assert token_client.get.await_count == 2


@pytest.mark.asyncio
async def test_request_qrcode_raises_share_error_on_wechat_error():
    token_client = _fake_client(get_response=_FakeResponse(json_data=TOKEN_PAYLOAD))
    qrcode_client = _fake_client(
        post_response=_FakeResponse(
            content=b'{"errcode":45009,"errmsg":"reach max api daily quota"}',
            headers={"content-type": "application/json"},
            json_data={"errcode": 45009, "errmsg": "reach max api daily quota"},
        )
    )
    with _patch_async_client(token_client, qrcode_client):
        with pytest.raises(ShareError):
            await wechat_qrcode_service.request_qrcode("quiz_abc123def456")


@pytest.mark.asyncio
async def test_request_qrcode_raises_share_error_on_network_failure():
    token_client = _fake_client(get_response=_FakeResponse(json_data=TOKEN_PAYLOAD))
    qrcode_client = _fake_client(post_side_effect=httpx.ConnectError("boom"))
    with _patch_async_client(token_client, qrcode_client):
        with pytest.raises(ShareError):
            await wechat_qrcode_service.request_qrcode("quiz_abc123def456")


@pytest.mark.asyncio
async def test_generate_poster_qrcode_uploads_to_cos():
    with patch.object(
        wechat_qrcode_service, "request_qrcode", AsyncMock(return_value=PNG_BYTES)
    ), patch.object(
        wechat_qrcode_service.cos_service,
        "upload_image_bytes",
        AsyncMock(return_value="https://cos.example.com/qrcode.png"),
    ) as upload:
        url = await wechat_qrcode_service.generate_poster_qrcode("quiz_abc123def456")

    assert url == "https://cos.example.com/qrcode.png"
    key = upload.await_args.args[1]
    assert "share-qrcodes/quiz_abc123def456/" in key
    assert key.endswith(".png")


@pytest.mark.asyncio
async def test_generate_poster_qrcode_uses_detected_image_type():
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"0" * 32
    with patch.object(
        wechat_qrcode_service, "request_qrcode", AsyncMock(return_value=jpeg_bytes)
    ), patch.object(
        wechat_qrcode_service.cos_service,
        "upload_image_bytes",
        AsyncMock(return_value="https://cos.example.com/qrcode.jpg"),
    ) as upload:
        await wechat_qrcode_service.generate_poster_qrcode("quiz_abc123def456")

    assert upload.await_args.args[1].endswith(".jpg")
    assert upload.await_args.kwargs["content_type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_missing_wechat_configuration_raises_share_error():
    settings = MagicMock()
    settings.wechat_app_id = ""
    settings.wechat_app_secret = ""
    with patch.object(wechat_qrcode_service, "get_settings", MagicMock(return_value=settings)):
        with pytest.raises(ShareError):
            await wechat_qrcode_service.get_access_token()