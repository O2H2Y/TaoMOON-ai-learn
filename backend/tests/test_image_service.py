"""题目配图生成服务单元测试"""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.quiz import Question, QuestionOption
from app.services import image_service


def _make_question(qid: str) -> Question:
    return Question(
        id=qid,
        type="single",
        stem=f"苹果的英文单词是什么？（{qid}）",
        options=[
            QuestionOption(key="A", text="apple"),
            QuestionOption(key="B", text="banana"),
            QuestionOption(key="C", text="orange"),
            QuestionOption(key="D", text="grape"),
        ],
        answer=["A"],
        explanation="苹果的英文是 apple。",
        knowledge_point="英语单词-水果",
        difficulty="easy",
    )


class TestBuildImagePrompt:
    def test_prompt_contains_stem_and_knowledge_point(self):
        question = _make_question("q1")
        prompt = image_service.build_image_prompt(question)

        assert question.stem in prompt
        assert question.knowledge_point in prompt
        assert "文字" in prompt  # 要求画面不含文字/答案


class TestDeriveImageBaseUrl:
    def test_replaces_compatible_mode_suffix(self):
        result = image_service._derive_image_base_url(
            "https://xxx.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        )
        assert result == "https://xxx.cn-beijing.maas.aliyuncs.com/api/v1"

    def test_leaves_other_urls_unchanged(self):
        result = image_service._derive_image_base_url("https://dashscope.aliyuncs.com/api/v1")
        assert result == "https://dashscope.aliyuncs.com/api/v1"


@pytest.mark.asyncio
class TestGenerateImagesForQuiz:
    async def test_anonymous_user_skips_generation(self):
        questions = [_make_question("q1"), _make_question("q2")]

        image_map, notice = await image_service.generate_images_for_quiz(
            questions, user_id=None, quiz_id="quiz_1"
        )

        assert image_map == {}
        assert "登录" in notice

    async def test_quota_exhausted_skips_all(self):
        questions = [_make_question("q1"), _make_question("q2")]

        with patch(
            "app.services.image_service.image_repository.get_today_usage_count",
            new_callable=AsyncMock,
            return_value=20,
        ), patch(
            "app.services.image_service.generate_image_for_question",
            new_callable=AsyncMock,
        ) as mock_gen:
            image_map, notice = await image_service.generate_images_for_quiz(
                questions, user_id=1, quiz_id="quiz_1"
            )

        assert image_map == {}
        assert "已用完" in notice
        mock_gen.assert_not_called()

    async def test_partial_quota_only_generates_for_remaining(self):
        questions = [_make_question("q1"), _make_question("q2"), _make_question("q3")]

        with patch(
            "app.services.image_service.image_repository.get_today_usage_count",
            new_callable=AsyncMock,
            return_value=19,
        ), patch(
            "app.services.image_service.image_repository.log_image_generation",
            new_callable=AsyncMock,
        ), patch(
            "app.services.image_service.generate_image_for_question",
            new_callable=AsyncMock,
            return_value="https://example.com/img.png",
        ) as mock_gen:
            image_map, notice = await image_service.generate_images_for_quiz(
                questions, user_id=1, quiz_id="quiz_1"
            )

        assert len(image_map) == 1
        assert image_map["q1"] == "https://example.com/img.png"
        assert "剩余 1 张" in notice
        assert mock_gen.call_count == 1

    async def test_full_quota_generates_all_concurrently(self):
        questions = [_make_question("q1"), _make_question("q2")]

        async def fake_generate(question, quiz_id):
            return f"https://example.com/{question.id}.png"

        with patch(
            "app.services.image_service.image_repository.get_today_usage_count",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.services.image_service.image_repository.log_image_generation",
            new_callable=AsyncMock,
        ) as mock_log, patch(
            "app.services.image_service.generate_image_for_question",
            side_effect=fake_generate,
        ):
            image_map, notice = await image_service.generate_images_for_quiz(
                questions, user_id=1, quiz_id="quiz_1"
            )

        assert image_map == {
            "q1": "https://example.com/q1.png",
            "q2": "https://example.com/q2.png",
        }
        assert notice is None
        assert mock_log.call_count == 2

    async def test_individual_failure_does_not_affect_others(self):
        """某道题生图失败（返回 None）不影响其他题目正常拿到图片，也不抛异常"""
        questions = [_make_question("q1"), _make_question("q2")]

        async def fake_generate(question, quiz_id):
            if question.id == "q1":
                return None
            return "https://example.com/q2.png"

        with patch(
            "app.services.image_service.image_repository.get_today_usage_count",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.services.image_service.image_repository.log_image_generation",
            new_callable=AsyncMock,
        ), patch(
            "app.services.image_service.generate_image_for_question",
            side_effect=fake_generate,
        ):
            image_map, notice = await image_service.generate_images_for_quiz(
                questions, user_id=1, quiz_id="quiz_1"
            )

        assert image_map == {"q2": "https://example.com/q2.png"}


@pytest.mark.asyncio
class TestGenerateImageForQuestion:
    async def test_model_call_failure_returns_none(self):
        """生图模型调用失败时应返回 None 而不是抛异常"""
        question = _make_question("q1")

        with patch(
            "app.services.image_service.asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=RuntimeError("生图失败：InvalidParameter xxx"),
        ):
            result = await image_service.generate_image_for_question(question, "quiz_1")

        assert result is None

    async def test_download_failure_returns_none(self):
        """图片下载失败时应返回 None 而不是抛异常"""
        question = _make_question("q1")

        with patch(
            "app.services.image_service.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value="https://temp.example.com/img.png",
        ), patch(
            "app.services.image_service._download_image",
            new_callable=AsyncMock,
            side_effect=RuntimeError("下载失败"),
        ):
            result = await image_service.generate_image_for_question(question, "quiz_1")

        assert result is None

    async def test_cos_upload_failure_returns_none(self):
        """COS 上传失败时应返回 None 而不是抛异常"""
        question = _make_question("q1")

        with patch(
            "app.services.image_service.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value="https://temp.example.com/img.png",
        ), patch(
            "app.services.image_service._download_image",
            new_callable=AsyncMock,
            return_value=b"fake-bytes",
        ), patch(
            "app.services.image_service.upload_image_bytes",
            new_callable=AsyncMock,
            side_effect=RuntimeError("COS 上传失败"),
        ):
            result = await image_service.generate_image_for_question(question, "quiz_1")

        assert result is None

    async def test_success_returns_cos_url(self):
        question = _make_question("q1")

        with patch(
            "app.services.image_service.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value="https://temp.example.com/img.png",
        ), patch(
            "app.services.image_service._download_image",
            new_callable=AsyncMock,
            return_value=b"fake-bytes",
        ), patch(
            "app.services.image_service.upload_image_bytes",
            new_callable=AsyncMock,
            return_value="https://cos.example.com/quiz-images/quiz_1/q1.png",
        ):
            result = await image_service.generate_image_for_question(question, "quiz_1")

        assert result == "https://cos.example.com/quiz-images/quiz_1/q1.png"


class TestNativeImageGenerationRequest:
    """百炼「千问图像模型」原生同步接口：地址归一化、请求构造、响应解析"""

    @staticmethod
    def _settings(**overrides):
        from types import SimpleNamespace

        values = {
            "dashscope_image_base_url":
                "https://ws-example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "dashscope_base_url": "",
            "dashscope_image_api_key": "image-key",
            "dashscope_api_key": "fallback-key",
            "dashscope_image_model": "qwen-image-2.0",
            "image_gen_size": "512*512",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_normalizes_compatible_base_to_native_sync_endpoint(self):
        assert image_service._get_image_generation_url(self._settings()) == (
            "https://ws-example.cn-beijing.maas.aliyuncs.com/api/v1"
            "/services/aigc/multimodal-generation/generation"
        )

    def test_accepts_native_base_and_bare_domain(self):
        native = self._settings(
            dashscope_image_base_url="https://ws-example.cn-beijing.maas.aliyuncs.com/api/v1"
        )
        bare = self._settings(dashscope_image_base_url="https://dashscope.aliyuncs.com")

        assert image_service._get_image_generation_url(native) == (
            "https://ws-example.cn-beijing.maas.aliyuncs.com/api/v1"
            "/services/aigc/multimodal-generation/generation"
        )
        assert image_service._get_image_generation_url(bare) == (
            "https://dashscope.aliyuncs.com/api/v1"
            "/services/aigc/multimodal-generation/generation"
        )

    def test_falls_back_to_shared_base_url(self):
        settings = self._settings(
            dashscope_image_base_url="",
            dashscope_base_url="https://ws-example.cn-beijing.maas.aliyuncs.com/api/v1",
        )
        assert image_service._get_image_generation_url(settings).endswith(
            "/api/v1/services/aigc/multimodal-generation/generation"
        )

    def test_calls_native_endpoint_with_expected_payload(self):
        from types import SimpleNamespace

        response = SimpleNamespace(
            status_code=200,
            headers={},
            json=lambda: {
                "output": {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": [{"image": "https://temp.example.com/image.png"}],
                            },
                        }
                    ]
                },
                "usage": {"image_count": 1, "width": 512, "height": 512},
                "request_id": "req-ok",
            },
        )

        with patch(
            "app.services.image_service.get_settings", return_value=self._settings()
        ), patch(
            "app.services.image_service.requests.post", return_value=response
        ) as mock_post:
            result = image_service._call_image_model_sync("test prompt")

        assert result == "https://temp.example.com/image.png"
        mock_post.assert_called_once_with(
            "https://ws-example.cn-beijing.maas.aliyuncs.com/api/v1"
            "/services/aigc/multimodal-generation/generation",
            json={
                "model": "qwen-image-2.0",
                "input": {
                    "messages": [
                        {"role": "user", "content": [{"text": "test prompt"}]}
                    ]
                },
                "parameters": {
                    "size": "512*512",
                    "n": 1,
                    "prompt_extend": True,
                    "watermark": False,
                },
            },
            headers={
                "Authorization": "Bearer image-key",
                "Content-Type": "application/json",
            },
            timeout=120,
        )

    def test_provider_error_preserves_safe_diagnostics_without_prompt(self):
        from types import SimpleNamespace

        response = SimpleNamespace(
            status_code=400,
            headers={"x-dashscope-request-id": "req-header"},
            json=lambda: {
                "code": "InvalidParameter",
                "message": "url error, please check url",
                "request_id": "req-123",
            },
        )

        with patch(
            "app.services.image_service.get_settings", return_value=self._settings()
        ), patch(
            "app.services.image_service.requests.post", return_value=response
        ), patch("app.services.image_service.logger.error") as mock_log:
            with pytest.raises(RuntimeError, match="InvalidParameter"):
                image_service._call_image_model_sync("private prompt")

        fields = mock_log.call_args.kwargs
        assert fields["http_status"] == 400
        assert fields["provider_code"] == "InvalidParameter"
        assert fields["request_id"] == "req-123"
        assert "prompt" not in fields

    def test_falls_back_to_shared_api_key_when_image_key_is_empty(self):
        from types import SimpleNamespace

        response = SimpleNamespace(
            status_code=200,
            headers={},
            json=lambda: {
                "output": {
                    "choices": [{"message": {"content": [{"image": "https://a/b.png"}]}}]
                }
            },
        )

        with patch(
            "app.services.image_service.get_settings",
            return_value=self._settings(dashscope_image_api_key=""),
        ), patch(
            "app.services.image_service.requests.post", return_value=response
        ) as mock_post:
            image_service._call_image_model_sync("test prompt")

        assert mock_post.call_args.kwargs["headers"]["Authorization"] == (
            "Bearer fallback-key"
        )

    def test_invalid_success_payload_raises_without_dumping_response(self):
        from types import SimpleNamespace

        response = SimpleNamespace(
            status_code=200,
            headers={},
            json=lambda: {"output": {"choices": []}},
        )

        with patch(
            "app.services.image_service.get_settings", return_value=self._settings()
        ), patch(
            "app.services.image_service.requests.post", return_value=response
        ), pytest.raises(RuntimeError, match="URL"):
            image_service._call_image_model_sync("test prompt")


class TestRequestPacer:
    """请求匀速节流器：任意两次请求启动的间隔不低于配置值"""

    @pytest.mark.asyncio
    async def test_enforces_min_interval_between_requests(self):
        pacer = image_service._RequestPacer()
        clock = {"now": 1000.0}
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock["now"] += seconds

        with patch.object(
            image_service.time, "monotonic", side_effect=lambda: clock["now"]
        ), patch.object(
            image_service.asyncio,
            "sleep",
            new_callable=AsyncMock,
            side_effect=fake_sleep,
        ):
            await pacer.wait_turn(0.7)
            await pacer.wait_turn(0.7)
            await pacer.wait_turn(0.7)

        assert len(sleeps) == 2
        assert sum(sleeps) == pytest.approx(1.4)

    @pytest.mark.asyncio
    async def test_zero_interval_never_sleeps(self):
        pacer = image_service._RequestPacer()

        with patch.object(
            image_service.asyncio, "sleep", new_callable=AsyncMock
        ) as mock_sleep:
            await pacer.wait_turn(0)
            await pacer.wait_turn(0)

        mock_sleep.assert_not_awaited()


class TestRateLimitRetry:
    """限流（429）分类、指数退避重试与失败降级"""

    @staticmethod
    def _settings(**overrides):
        from types import SimpleNamespace

        values = {
            "dashscope_image_base_url": "https://example.com/compatible-mode/v1",
            "dashscope_base_url": "",
            "dashscope_image_api_key": "image-key",
            "dashscope_api_key": "fallback-key",
            "dashscope_image_model": "qwen-image-2.0",
            "image_gen_size": "512*512",
            "cos_upload_prefix": "quiz-images/",
            "image_gen_min_interval_seconds": 0.7,
            "image_gen_max_attempts": 3,
            "image_gen_retry_base_delay_seconds": 2.0,
            "image_gen_retry_max_delay_seconds": 30.0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    @staticmethod
    def _patch_success_path():
        return (
            patch(
                "app.services.image_service._pacer.wait_turn", new_callable=AsyncMock
            ),
            patch(
                "app.services.image_service.asyncio.sleep", new_callable=AsyncMock
            ),
            patch(
                "app.services.image_service._download_image",
                new_callable=AsyncMock,
                return_value=b"fake-bytes",
            ),
            patch(
                "app.services.image_service.upload_image_bytes",
                new_callable=AsyncMock,
                return_value="https://cos.example.com/quiz-images/q1.png",
            ),
        )

    def test_limit_requests_is_retryable_and_keeps_retry_after(self):
        from types import SimpleNamespace

        response = SimpleNamespace(
            status_code=429,
            headers={"Retry-After": "5"},
            json=lambda: {
                "error": {
                    "code": "limit_requests",
                    "message": "Requests rate limit exceeded, please try again later.",
                }
            },
        )

        with patch(
            "app.services.image_service.get_settings", return_value=self._settings()
        ), patch(
            "app.services.image_service.requests.post", return_value=response
        ), patch("app.services.image_service.logger.error"):
            with pytest.raises(image_service.ImageGenerationRetryableError) as exc_info:
                image_service._call_image_model_sync("test prompt")

        assert "limit_requests" in str(exc_info.value)
        assert exc_info.value.retry_after == 5.0

    def test_invalid_parameter_is_not_retryable(self):
        from types import SimpleNamespace

        response = SimpleNamespace(
            status_code=400,
            headers={},
            json=lambda: {
                "error": {"code": "InvalidParameter", "message": "unsupported size"}
            },
        )

        with patch(
            "app.services.image_service.get_settings", return_value=self._settings()
        ), patch(
            "app.services.image_service.requests.post", return_value=response
        ), patch("app.services.image_service.logger.error"):
            with pytest.raises(RuntimeError) as exc_info:
                image_service._call_image_model_sync("test prompt")

        assert not isinstance(exc_info.value, image_service.ImageGenerationRetryableError)

    def test_server_error_is_retryable(self):
        from types import SimpleNamespace

        response = SimpleNamespace(
            status_code=503,
            headers={},
            json=lambda: {"error": {"code": "InternalError", "message": "busy"}},
        )

        with patch(
            "app.services.image_service.get_settings", return_value=self._settings()
        ), patch(
            "app.services.image_service.requests.post", return_value=response
        ), patch("app.services.image_service.logger.error"):
            with pytest.raises(image_service.ImageGenerationRetryableError):
                image_service._call_image_model_sync("test prompt")

    @pytest.mark.asyncio
    async def test_retries_on_rate_limit_then_succeeds(self):
        question = _make_question("q1")
        pacer_patch, sleep_patch, download_patch, upload_patch = self._patch_success_path()

        with patch(
            "app.services.image_service.get_settings", return_value=self._settings()
        ), patch(
            "app.services.image_service._call_image_model_sync",
            side_effect=[
                image_service.ImageGenerationRetryableError(
                    "image generation failed: limit_requests rate limited"
                ),
                "https://temp.example.com/img.png",
            ],
        ) as mock_call, pacer_patch, sleep_patch as mock_sleep, download_patch, upload_patch:
            result = await image_service.generate_image_for_question(question, "quiz_1")

        assert result == "https://cos.example.com/quiz-images/q1.png"
        assert mock_call.call_count == 2
        mock_sleep.assert_awaited_once_with(2.0)

    @pytest.mark.asyncio
    async def test_retry_after_header_wins_over_backoff(self):
        question = _make_question("q1")
        pacer_patch, sleep_patch, download_patch, upload_patch = self._patch_success_path()

        with patch(
            "app.services.image_service.get_settings", return_value=self._settings()
        ), patch(
            "app.services.image_service._call_image_model_sync",
            side_effect=[
                image_service.ImageGenerationRetryableError("rate limited", retry_after=7.5),
                "https://temp.example.com/img.png",
            ],
        ), pacer_patch, sleep_patch as mock_sleep, download_patch, upload_patch:
            await image_service.generate_image_for_question(question, "quiz_1")

        mock_sleep.assert_awaited_once_with(7.5)

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts(self):
        question = _make_question("q1")

        with patch(
            "app.services.image_service.get_settings",
            return_value=self._settings(image_gen_max_attempts=3),
        ), patch(
            "app.services.image_service._call_image_model_sync",
            side_effect=image_service.ImageGenerationRetryableError("rate limited"),
        ) as mock_call, patch(
            "app.services.image_service._pacer.wait_turn", new_callable=AsyncMock
        ), patch(
            "app.services.image_service.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            result = await image_service.generate_image_for_question(question, "quiz_1")

        assert result is None
        assert mock_call.call_count == 3
        assert mock_sleep.await_count == 2  # 最后一次失败后不再等待

    @pytest.mark.asyncio
    async def test_non_retryable_error_is_not_retried(self):
        question = _make_question("q1")

        with patch(
            "app.services.image_service.get_settings", return_value=self._settings()
        ), patch(
            "app.services.image_service._call_image_model_sync",
            side_effect=RuntimeError("image generation failed: InvalidParameter bad size"),
        ) as mock_call, patch(
            "app.services.image_service._pacer.wait_turn", new_callable=AsyncMock
        ):
            result = await image_service.generate_image_for_question(question, "quiz_1")

        assert result is None
        assert mock_call.call_count == 1


class TestBatchSummaryLog:
    """批次汇总日志：便于线上快速定位“少了几张图”"""

    @pytest.mark.asyncio
    async def test_logs_success_and_failure_counts(self):
        questions = [_make_question("q1"), _make_question("q2")]

        async def fake_generate(question, quiz_id):
            return None if question.id == "q2" else "https://example.com/q1.png"

        with patch(
            "app.services.image_service.image_repository.get_today_usage_count",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.services.image_service.image_repository.log_image_generation",
            new_callable=AsyncMock,
        ), patch(
            "app.services.image_service.generate_image_for_question",
            side_effect=fake_generate,
        ), patch("app.services.image_service.logger.info") as mock_info:
            image_map, _ = await image_service.generate_images_for_quiz(
                questions, user_id=1, quiz_id="quiz_1"
            )

        assert image_map == {"q1": "https://example.com/q1.png"}
        summaries = [
            call
            for call in mock_info.call_args_list
            if call.args and call.args[0] == "image_batch_summary"
        ]
        assert len(summaries) == 1
        assert summaries[0].kwargs["succeeded"] == 1
        assert summaries[0].kwargs["failed"] == 1


class TestAiImageSelection:
    """AI 自主判断是否需要配图：只为标记为需要配图的题目生图"""

    @staticmethod
    def _question(qid: str, need_image):
        question = _make_question(qid)
        question.need_image = need_image
        return question

    def test_only_flagged_questions_are_selected(self):
        questions = [
            self._question("q1", True),
            self._question("q2", False),
            self._question("q3", True),
        ]

        selected = image_service._select_questions_for_images(questions, "quiz_1")

        assert [q.id for q in selected] == ["q1", "q3"]

    def test_all_false_means_no_images(self):
        questions = [self._question("q1", False), self._question("q2", False)]

        assert image_service._select_questions_for_images(questions, "quiz_1") == []

    def test_unjudged_questions_fall_back_to_all(self):
        questions = [_make_question("q1"), _make_question("q2")]

        selected = image_service._select_questions_for_images(questions, "quiz_1")

        assert [q.id for q in selected] == ["q1", "q2"]

    @pytest.mark.asyncio
    async def test_generate_images_only_for_flagged_questions(self):
        questions = [self._question("q1", True), self._question("q2", False)]

        async def fake_generate(question, quiz_id):
            return f"https://example.com/{question.id}.png"

        with patch(
            "app.services.image_service.image_repository.get_today_usage_count",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.services.image_service.image_repository.log_image_generation",
            new_callable=AsyncMock,
        ), patch(
            "app.services.image_service.generate_image_for_question",
            side_effect=fake_generate,
        ) as mock_generate:
            image_map, notice = await image_service.generate_images_for_quiz(
                questions, user_id=1, quiz_id="quiz_1"
            )

        assert image_map == {"q1": "https://example.com/q1.png"}
        assert notice is None
        assert mock_generate.call_count == 1

    @pytest.mark.asyncio
    async def test_all_false_skips_generation_entirely(self):
        questions = [self._question("q1", False), self._question("q2", False)]

        with patch(
            "app.services.image_service.image_repository.get_today_usage_count",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.services.image_service.generate_image_for_question",
            new_callable=AsyncMock,
        ) as mock_generate:
            image_map, notice = await image_service.generate_images_for_quiz(
                questions, user_id=1, quiz_id="quiz_1"
            )

        assert image_map == {}
        assert notice is None
        mock_generate.assert_not_called()
