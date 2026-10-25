"""报告 Chain"""

import json

from langchain_core.prompts import ChatPromptTemplate

from app.llm.langchain_factory import get_chat_model
from app.llm.structured import invoke_structured
from app.models.quiz import Question, AnswerRecord
from app.models.report import ReportOutput
from app.prompts.report_prompt import REPORT_HUMAN_PROMPT, REPORT_SYSTEM_PROMPT


def validate_report(report: ReportOutput) -> list[str]:
    problems: list[str] = []
    if not 0 <= report.accuracy <= 100:
        problems.append(f"accuracy_out_of_range {report.accuracy}")
    if not report.three_line_summary:
        problems.append("empty_summary")
    if not report.share_quote.strip():
        problems.append("empty_share_quote")
    return problems


async def generate_report(
    topic: str,
    questions: list[Question],
    answer_records: list[AnswerRecord],
    score_summary: dict,
) -> ReportOutput:
    llm = get_chat_model(temperature=0.5)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", REPORT_SYSTEM_PROMPT),
            ("human", REPORT_HUMAN_PROMPT),
        ]
    )

    inputs = {
        "topic": topic,
        "quiz_json": json.dumps([q.model_dump() for q in questions], ensure_ascii=False),
        "answer_records": json.dumps([r.model_dump() for r in answer_records], ensure_ascii=False),
        "score_summary": json.dumps(score_summary, ensure_ascii=False),
    }

    return await invoke_structured(
        prompt,
        llm,
        ReportOutput,
        inputs,
        stage_name="report_generate",
        validator=validate_report,
    )
