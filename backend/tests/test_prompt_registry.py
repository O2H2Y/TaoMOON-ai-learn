"""Prompt 版本登记表测试：改了 prompt 文本必须同步升版本号并更新指纹。"""

from app.prompts import registry


def test_every_prompt_has_version_and_fingerprint():
    assert set(registry.PROMPT_VERSIONS) == set(registry.PROMPT_FINGERPRINTS)


def test_fingerprints_match_current_prompt_text():
    current = registry.current_fingerprints()
    mismatched = {k: v for k, v in current.items() if registry.PROMPT_FINGERPRINTS[k] != v}
    assert not mismatched, (
        f"Prompt 文本已变更但登记表未更新：{mismatched}。"
        "请升级 PROMPT_VERSIONS 中对应版本号，并运行 `python -m app.prompts.registry` 更新指纹。"
    )


def test_prompt_version_tag_format():
    tag = registry.prompt_version_tag()
    assert tag.startswith("quiz-")
    assert len(tag) <= 64  # 与 quiz_sessions.prompt_version 列宽一致
