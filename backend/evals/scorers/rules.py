"""规则类评分（零 API 成本，可在 CI 中对缓存结果重复运行）。

对每局出题输出逐题检查 prompt 中写明的硬约束，并计算若干偏差指标。
"""

from __future__ import annotations

from typing import Any

JUDGE_OPTIONS = [("A", "正确"), ("B", "错误")]


def _bigrams(s: str) -> set[str]:
    s = "".join(s.split())
    return {s[i : i + 2] for i in range(len(s) - 1)} or {s}


def jaccard(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / max(1, len(x | y))


def check_question(q: dict[str, Any], requested_difficulty: str) -> list[str]:
    """返回该题违反的约束列表（空列表 = 合规）。"""
    v: list[str] = []
    keys = [o.get("key") for o in q.get("options", [])]
    texts = [str(o.get("text", "")).strip() for o in q.get("options", [])]
    ans = q.get("answer", [])
    qtype = q.get("type")

    if not str(q.get("stem", "")).strip():
        v.append("empty_stem")
    if not str(q.get("explanation", "")).strip():
        v.append("empty_explanation")
    if len(set(keys)) != len(keys):
        v.append("duplicate_option_key")
    if len(set(texts)) != len(texts):
        v.append("duplicate_option_text")
    if keys != [chr(ord("A") + i) for i in range(len(keys))]:
        v.append("option_keys_not_sequential")
    if not ans:
        v.append("empty_answer")
    if any(a not in keys for a in ans):
        v.append("answer_not_in_options")
    if len(set(ans)) != len(ans):
        v.append("duplicate_answer")

    if qtype == "single":
        if len(ans) != 1:
            v.append("single_answer_count")
        if len(keys) < 3:
            v.append("single_too_few_options")
    elif qtype == "multiple":
        if len(ans) < 2:
            v.append("multiple_lt2_answers")
        if len(ans) >= len(keys) and keys:
            v.append("multiple_all_correct")
    elif qtype == "judge":
        if [(k, t) for k, t in zip(keys, texts)] != JUDGE_OPTIONS:
            v.append("judge_options_not_AB")
        if len(ans) != 1:
            v.append("judge_answer_count")

    if requested_difficulty != "mixed" and q.get("difficulty") != requested_difficulty:
        v.append("difficulty_mismatch")
    return v


def score_quiz(item: dict[str, Any], questions: list[dict[str, Any]] | None) -> dict[str, Any]:
    """单局规则评分。questions=None 表示生成失败（解析失败/异常）。"""
    if questions is None:
        return {"generated": False}

    n_req = item["question_count"]
    per_q = [check_question(q, item["difficulty"]) for q in questions]
    types = [q.get("type") for q in questions]
    ids = [q.get("id") for q in questions]

    # 局内近重复：题干 bigram Jaccard > 0.6
    dup_pairs = 0
    stems = [str(q.get("stem", "")) for q in questions]
    for i in range(len(stems)):
        for j in range(i + 1, len(stems)):
            if jaccard(stems[i], stems[j]) > 0.6:
                dup_pairs += 1

    # 单选题：正确选项是否为最长选项（长度线索偏差）、答案位置
    single_longest = []
    single_pos = []
    for q in questions:
        if q.get("type") == "single" and len(q.get("answer", [])) == 1:
            opts = {o.get("key"): str(o.get("text", "")) for o in q.get("options", [])}
            a = q["answer"][0]
            if a in opts and opts:
                lens = {k: len(t) for k, t in opts.items()}
                mx = max(lens.values())
                single_longest.append(lens[a] == mx and list(lens.values()).count(mx) == 1)
                single_pos.append(a)

    violations = sorted({x for vs in per_q for x in vs})
    count_ok = len(questions) == n_req
    ids_ok = len(set(ids)) == len(ids)
    all_types = n_req < 5 or {"single", "multiple", "judge"} <= set(types)
    return {
        "generated": True,
        "n_questions": len(questions),
        "count_ok": count_ok,
        "ids_unique": ids_ok,
        "type_mix_ok": all_types,
        "type_counts": {t: types.count(t) for t in ("single", "multiple", "judge")},
        "question_violations": per_q,
        "n_bad_questions": sum(1 for vs in per_q if vs),
        "violations": violations,
        # 整局格式合规：题数对 + id 唯一 + 每题无违规
        "format_ok": count_ok and ids_ok and not violations,
        "intra_dup_pairs": dup_pairs,
        "single_answer_is_longest": single_longest,
        "single_answer_positions": single_pos,
    }
