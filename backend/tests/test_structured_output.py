"""结构化输出 + 校验失败重试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from app.llm.quiz_chain import validate_quiz
from app.llm.structured import StructuredOutputError, invoke_structured
from app.models.quiz import QuizOutput
from app.observability import start_run


def _q(qid="q1", qtype="single", answer=("A",), n_opts=4):
    return {
        "id": qid, "type": qtype, "stem": "题干",
        "options": [{"key": chr(65 + i), "text": f"选项{i}"} for i in range(n_opts)],
        "answer": list(answer), "explanation": "讲解",
        "knowledge_point": "知识点", "difficulty": "easy",
    }


def _quiz(*qs):
    return QuizOutput.model_validate({"title": "T", "summary": "S", "questions": list(qs)})


def _out(parsed=None, err=None, content=""):
    raw = AIMessage(content=content, usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
    return {"raw": raw, "parsed": parsed, "parsing_error": err}


def _llm_with_chain(outputs):
    chain = MagicMock()
    chain.ainvoke = AsyncMock(side_effect=outputs)
    prompt = MagicMock()
    prompt.__or__ = MagicMock(return_value=chain)
    llm = MagicMock()
    llm.model_name = "deepseek-flash"
    return prompt, llm, chain


@pytest.mark.asyncio
async def test_uses_json_mode_with_raw():
    prompt, llm, chain = _llm_with_chain([_out(parsed=_quiz(_q()))])
    result = await invoke_structured(prompt, llm, QuizOutput, {}, "quiz_generate")
    assert result.title == "T"
    llm.with_structured_output.assert_called_once_with(QuizOutput, method="json_mode", include_raw=True)
    assert chain.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_retries_once_on_parsing_error_then_succeeds():
    prompt, llm, chain = _llm_with_chain([_out(err=ValueError("bad json")), _out(parsed=_quiz(_q()))])
    with start_run("quiz") as run:
        result = await invoke_structured(prompt, llm, QuizOutput, {}, "quiz_generate")
    assert result.questions[0].id == "q1"
    assert chain.ainvoke.await_count == 2
    assert run.metadata["quiz_generate_attempts"] == 2
    assert sum(u.input_tokens for u in run.usage) == 20  # 两次尝试的 token 都记账


@pytest.mark.asyncio
async def test_retries_on_validator_problem():
    bad = _quiz(_q(answer=("E",)))  # 答案不在选项里
    good = _quiz(_q())
    prompt, llm, chain = _llm_with_chain([_out(parsed=bad), _out(parsed=good)])
    result = await invoke_structured(
        prompt, llm, QuizOutput, {}, "quiz_generate", validator=lambda q: validate_quiz(q, 1)
    )
    assert result.questions[0].answer == ["A"]
    assert chain.ainvoke.await_count == 2


@pytest.mark.asyncio
async def test_raises_after_max_attempts():
    prompt, llm, chain = _llm_with_chain([_out(err=ValueError("x")), _out(err=ValueError("y"))])
    with start_run("quiz") as run:
        with pytest.raises(StructuredOutputError):
            await invoke_structured(prompt, llm, QuizOutput, {}, "quiz_generate")
    assert chain.ainvoke.await_count == 2
    assert run.metadata["quiz_generate_failed"]


@pytest.mark.asyncio
async def test_fallback_parses_json_from_content():
    content = '```json\n{"title": "T", "summary": "S", "questions": []}\n```'
    prompt, llm, _ = _llm_with_chain([_out(content=content)])
    result = await invoke_structured(prompt, llm, QuizOutput, {}, "quiz_generate")
    assert result.title == "T"


def test_validate_quiz_rules():
    assert validate_quiz(_quiz(_q(), _q("q2")), 2) == []
    assert any("count" in p for p in validate_quiz(_quiz(_q()), 2))
    assert any("single_answer_count" in p for p in validate_quiz(_quiz(_q(answer=("A", "B"))), 1))
    assert any("multiple_lt2" in p for p in validate_quiz(_quiz(_q(qtype="multiple")), 1))
    assert any("judge_option_count" in p for p in validate_quiz(_quiz(_q(qtype="judge")), 1))
    # 多选全对按用户决定不拦截
    assert validate_quiz(_quiz(_q(qtype="multiple", answer=("A", "B", "C", "D"))), 1) == []
