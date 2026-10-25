"""异源 LLM 评审（通义千问，经 DashScope OpenAI 兼容接口）。出题用 DeepSeek，评审用 Qwen，避免自评偏差。

两步：
1. 盲答（solver）：只给题干和选项、不给答案，让评审模型独立作答 → 与题目给定答案比对。
   盲答与答案不一致的题是"答案可能错 / 题目有歧义"的高召回信号。
2. 带证据评审（judge）：给联网证据摘要 + 用户学习主题 + 全部题目（含答案与讲解），逐题输出结构化判断。

所有输出只算 silver 标签；judge 与人工抽检的一致率在 calibration 中报告，未校准前不当作 GT。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
JUDGE_MODEL = "qwen3.7-plus"
SOLVER_MODEL = "qwen3.7-plus"
JUDGE_VERSION = "judge-v1"
# 元 / 百万 token（qwen3.7-plus，0<Token≤256K 原价；来源 https://help.aliyun.com/zh/model-studio/model-pricing ，2026-10-24 抓取）
QWEN_PRICES = {"qwen3.7-plus": (2.0, 8.0), "qwen3.8-flash": (0.8, 2.7)}

SOLVER_SYSTEM = "你是一名认真的考生。请独立作答下面每一道题，只输出 JSON。"
SOLVER_PROMPT = """请独立作答以下题目。单选题和判断题只选 1 个选项；多选题选出所有正确选项。
如果你认为题目本身有错误或没有正确选项，也请给出你认为最合理的选择，并把 confident 设为 false。

只输出 JSON：{{"answers": [{{"id": "q1", "answer": ["A"], "confident": true}}]}}

题目：
{questions}"""

JUDGE_SYSTEM = "你是一名严谨的出题质量审核专家。你只输出合法 JSON。"
JUDGE_PROMPT = """用户想学习的内容是：
<<<
{user_input}
>>>

出题时使用的联网参考资料（可能为空、可能含报错信息）：
<<<
{evidence}
>>>

下面是 AI 生成的题目（含标准答案和讲解）：
{questions}

请逐题审核，每题给出：
- answer_correct: 标准答案是否正确。"yes" / "no" / "unsure"（依据参考资料和可靠常识；无法确定才用 unsure）
- unique_answer: 题目是否只有标准答案这一种合理解读（无歧义、无第二个也说得通的选项）。true / false
- explanation_faithful: 讲解中是否没有事实错误或编造（与参考资料或可靠常识一致）。true / false
- hallucination: 题干、选项或讲解中是否出现了参考资料没有、且你判断为错误或无法核实的具体事实（人名、日期、数字、版本号等）。true / false
- distractor_quality: 干扰项质量。3=都似是而非且明确错误；2=有一个明显凑数；1=多个凑数或干扰项其实也对。判断题固定填 2。
- on_topic: 是否紧扣用户想学的内容（含用户的真实意图，例如用户给了 URL 就看是否围绕该页面主题）。true / false
- issue: 如有问题，用一句话说明；没有问题填空字符串。

只输出 JSON：{{"items": [{{"id": "q1", "answer_correct": "yes", "unique_answer": true, "explanation_faithful": true, "hallucination": false, "distractor_quality": 3, "on_topic": true, "issue": ""}}]}}"""


def _fmt_questions(questions: list[dict], with_answer: bool) -> str:
    out = []
    for q in questions:
        lines = [f"[{q.get('id')}] ({q.get('type')}) {q.get('stem')}"]
        for o in q.get("options", []):
            lines.append(f"  {o.get('key')}. {o.get('text')}")
        if with_answer:
            lines.append(f"  标准答案：{','.join(q.get('answer', []))}")
            lines.append(f"  讲解：{q.get('explanation')}")
            lines.append(f"  知识点：{q.get('knowledge_point')}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


async def _chat(client: httpx.AsyncClient, model: str, system: str, user: str) -> tuple[dict, dict]:
    r = await client.post(
        f"{BASE_URL}/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "enable_thinking": False,
        },
        headers={"Authorization": f"Bearer {os.environ.get('DASHSCOPE_API_KEY', 'x')}"},
        timeout=180,
    )
    r.raise_for_status()
    d = r.json()
    usage = d.get("usage", {})
    return _parse_json(d["choices"][0]["message"]["content"]), {
        "model": model,
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
    }


def evidence_text(rec: dict, limit: int = 12000) -> str:
    ctxs = rec["record"]["contexts"]
    if not ctxs:
        return ""
    c = ctxs[0]
    parts = [str(c.get("content") or "")]
    for e in c.get("evidence", []):
        parts.append(str(e.get("content", ""))[:4000])
    return "\n\n---\n\n".join(p for p in parts if p)[:limit]


def cost(usages: list[dict]) -> float:
    total = 0.0
    for u in usages:
        pin, pout = QWEN_PRICES.get(u["model"], (2.0, 8.0))
        total += (u["input_tokens"] * pin + u["output_tokens"] * pout) / 1e6
    return total


async def judge_quiz(client: httpx.AsyncClient, rec: dict) -> dict[str, Any]:
    qs = rec["response"]["questions"]
    solver, u1 = await _chat(client, SOLVER_MODEL, SOLVER_SYSTEM, SOLVER_PROMPT.format(questions=_fmt_questions(qs, False)))
    judged, u2 = await _chat(
        client,
        JUDGE_MODEL,
        JUDGE_SYSTEM,
        JUDGE_PROMPT.format(user_input=rec["item"]["user_input"], evidence=evidence_text(rec) or "（无）",
                            questions=_fmt_questions(qs, True)),
    )
    solved = {a.get("id"): a for a in solver.get("answers", [])}
    items = {j.get("id"): j for j in judged.get("items", [])}
    per_q = []
    for q in qs:
        s = solved.get(q.get("id"), {})
        per_q.append({
            "id": q.get("id"),
            "type": q.get("type"),
            "solver_answer": s.get("answer"),
            "solver_confident": s.get("confident"),
            "solver_agrees": sorted(s.get("answer") or []) == sorted(q.get("answer") or []),
            "judge": items.get(q.get("id")),
        })
    return {"judge_version": JUDGE_VERSION, "per_question": per_q, "usage": [u1, u2], "cost_cny": cost([u1, u2])}
