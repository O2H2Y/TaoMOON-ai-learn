"""出题 Chain"""

from typing import Any

import structlog
from langchain_core.prompts import ChatPromptTemplate

from app.llm.langchain_factory import get_chat_model
from app.llm.structured import invoke_structured
from app.models.quiz import QuizOutput
from app.prompts.quiz_prompt import QUIZ_HUMAN_PROMPT, QUIZ_SYSTEM_PROMPT, SEARCH_CONTEXT_TEMPLATE

logger = structlog.get_logger()


def validate_quiz(quiz: QuizOutput, question_count: int) -> list[str]:
    """出题结果的硬性校验：只拦会让前端展示或判分出错的结构问题。

    注意：多选题“全部选项都正确”、答案位置分布等属于出题风格问题，按 2026-10-25 的决定不在这里拦截。
    """
    problems: list[str] = []
    if len(quiz.questions) != question_count:
        problems.append(f"count {len(quiz.questions)}!={question_count}")
    for q in quiz.questions:
        keys = [o.key for o in q.options]
        tag = q.id or "?"
        if len(set(keys)) != len(keys):
            problems.append(f"{tag}: duplicate_option_key")
        if not q.answer:
            problems.append(f"{tag}: empty_answer")
        elif any(a not in keys for a in q.answer):
            problems.append(f"{tag}: answer_not_in_options")
        if q.type in ("single", "judge") and len(q.answer) != 1:
            problems.append(f"{tag}: {q.type}_answer_count")
        if q.type == "multiple" and len(q.answer) < 2:
            problems.append(f"{tag}: multiple_lt2_answers")
        if q.type == "judge" and len(keys) != 2:
            problems.append(f"{tag}: judge_option_count")
    return problems


async def generate_quiz(
    user_input: str,
    question_count: int = 5,
    difficulty: str = "mixed",
    search_context: str = "",
) -> QuizOutput:
    llm = get_chat_model(temperature=0.4)

    # 构建搜索上下文段落
    search_context_section = (
        SEARCH_CONTEXT_TEMPLATE.format(search_context=search_context)
        if search_context
        else ""
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", QUIZ_SYSTEM_PROMPT),
            ("human", QUIZ_HUMAN_PROMPT),
        ]
    )

    inputs: dict[str, Any] = {
        "user_input": user_input,
        "question_count": question_count,
        "difficulty": difficulty,
        "search_context_section": search_context_section,
    }

    quiz = await invoke_structured(
        prompt,
        llm,
        QuizOutput,
        inputs,
        stage_name="quiz_generate",
        validator=lambda q: validate_quiz(q, question_count),
    )
    # 配图 URL 只能由生图服务填写，模型给的一律丢弃
    for q in quiz.questions:
        q.image_url = None
    return quiz
