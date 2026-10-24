"""生成人工抽检标注包（盲标：不展示 judge 判断），并在回收后计算 judge 与人工的一致率。

抽样：从 baseline 题目中分层抽 60 题 = 30 题 judge/盲答标记有问题 + 30 题随机（固定种子）。
有问题题过采样是为了估准 judge 的"误报率"；报告一致率时按两层的真实比例加权还原。

用法：
  python -m evals.calibration make  --tag baseline-2026-10-24 --out ../../annotation/calib_v1.xlsx
  python -m evals.calibration score --tag baseline-2026-10-24 --xlsx ../../annotation/calib_v1_filled.xlsx
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from evals.scorers.judge import JUDGE_VERSION, evidence_text

RESULTS = Path(__file__).parent / "results"
SEED = 20261024


def flagged(pq: dict) -> bool:
    j = pq.get("judge") or {}
    return (not pq["solver_agrees"]) or j.get("answer_correct") != "yes" or not j.get("unique_answer", True) \
        or not j.get("explanation_faithful", True) or j.get("hallucination") or not j.get("on_topic", True)


def collect(tag: str):
    man = json.loads((RESULTS / tag / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for k in man["cache_keys"]:
        jp = RESULTS / "judge" / JUDGE_VERSION / f"{k}.json"
        if not jp.exists():
            continue
        rec = json.loads((RESULTS / "runs" / f"{k}.json").read_text(encoding="utf-8"))
        jd = json.loads(jp.read_text(encoding="utf-8"))
        qs = {q["id"]: q for q in rec["response"]["questions"]}
        for pq in jd["per_question"]:
            rows.append((k, rec, qs[pq["id"]], pq))
    return rows


def make(tag: str, out: str, n_flag: int = 30, n_rand: int = 30) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    rows = collect(tag)
    rng = random.Random(SEED)
    fl = [r for r in rows if flagged(r[3])]
    ok = [r for r in rows if not flagged(r[3])]
    pick = rng.sample(fl, min(n_flag, len(fl))) + rng.sample(ok, min(n_rand, len(ok)))
    rng.shuffle(pick)

    wb = Workbook()
    ws = wb.active
    ws.title = "标注"
    head = ["uid", "学习主题", "题型", "题干与选项", "标准答案", "讲解", "参考资料摘录",
            "答案正确?(yes/no/unsure)", "唯一答案?(y/n)", "讲解无事实错误?(y/n)", "有编造?(y/n)",
            "干扰项质量(1-3,判断题留空)", "紧扣主题?(y/n)", "备注"]
    ws.append(head)
    meta = {}
    for i, (k, rec, q, pq) in enumerate(pick, 1):
        uid = f"c{i:02d}"
        meta[uid] = {"cache_key": k, "qid": q["id"], "stratum": "flagged" if flagged(pq) else "random"}
        body = q["stem"] + "\n" + "\n".join(f"{o['key']}. {o['text']}" for o in q["options"])
        ws.append([uid, rec["item"]["user_input"], q["type"], body, ",".join(q["answer"]), q["explanation"],
                   evidence_text(rec, 3000)])
    widths = [6, 22, 8, 50, 8, 50, 60, 12, 10, 12, 10, 12, 10, 24]
    for col, w in zip("ABCDEFGHIJKLMN", widths):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = "B2"
    guide = wb.create_sheet("说明")
    for line in [
        "每行一道 AI 生成的题。请只看题目本身和资料，独立判断后填写 H–M 列。",
        "答案正确：标准答案是否正确（可用常识或自行查证）；拿不准填 unsure。",
        "唯一答案：是否不存在第二个也说得通的选项、题干无歧义。",
        "讲解无事实错误：讲解里有任何错误事实就填 n。",
        "有编造：出现了查不到或错误的具体人名/日期/数字/版本号就填 y（资料里没有但确实正确的，填 n）。",
        "干扰项质量：3=都似是而非且明确错误；2=有一个明显凑数；1=多个凑数或干扰项其实也对。判断题留空。",
        "紧扣主题：题目是否围绕用户想学的内容。",
        "预计 60 题 × 1–2 分钟。",
    ]:
        guide.append([line])
    guide.column_dimensions["A"].width = 110
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    (Path(out).with_suffix(".meta.json")).write_text(json.dumps(
        {"tag": tag, "judge_version": JUDGE_VERSION, "seed": SEED, "n_flagged_total": len(fl), "n_ok_total": len(ok),
         "items": meta}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out}: {len(pick)} items (flagged pool {len(fl)}, ok pool {len(ok)})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make"])
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    make(a.tag, a.out)
