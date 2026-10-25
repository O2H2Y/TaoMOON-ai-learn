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


# ---------------------------------------------------------------- score

YES = {"y", "yes", "是", "true", "1"}
NO = {"n", "no", "否", "false", "0"}

# 维度：(名称, 人工取值函数, judge 取值函数)；值为 True 表示"有问题"，None 表示不可比
def _yn(v):
    v = str(v).strip().lower() if v is not None else ""
    return True if v in YES else False if v in NO else None


def human_labels(row: dict) -> dict:
    ac = str(row["answer_correct"] or "").strip().lower()
    h = {
        "answer_wrong": None if ac in ("unsure", "") else ac == "no",
        "not_unique": None if _yn(row["unique"]) is None else not _yn(row["unique"]),
        "expl_error": None if _yn(row["faithful"]) is None else not _yn(row["faithful"]),
        "hallucination": _yn(row["halluc"]),
        "off_topic": None if _yn(row["on_topic"]) is None else not _yn(row["on_topic"]),
    }
    dims = [h[k] for k in h]
    h["bad"] = None if any(d is None for d in dims) else any(dims)
    return h


def judge_labels(pq: dict) -> dict:
    j = pq.get("judge") or {}
    return {
        "answer_wrong": j.get("answer_correct") == "no",
        "not_unique": not j.get("unique_answer", True),
        "expl_error": not j.get("explanation_faithful", True),
        "hallucination": bool(j.get("hallucination")),
        "off_topic": not j.get("on_topic", True),
        "bad": flagged(pq),  # 与 report.py 的"综合好题"互补：含盲答不一致
    }


DIMS = ["bad", "answer_wrong", "not_unique", "expl_error", "hallucination", "off_topic"]


def read_filled(xlsx: str) -> dict[str, dict]:
    from openpyxl import load_workbook

    ws = load_workbook(xlsx, read_only=True)["标注"]
    out = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or not r[0]:
            continue
        out[str(r[0])] = {"type": r[2], "answer_correct": r[7], "unique": r[8], "faithful": r[9],
                          "halluc": r[10], "distractor": r[11], "on_topic": r[12], "note": r[13]}
    return out


def _weighted_stats(items: list[dict], w: dict[str, float], dim: str) -> dict:
    """items: {stratum, h, j}；返回按层权重还原的总体估计。"""
    cm = {"tp": 0.0, "fp": 0.0, "fn": 0.0, "tn": 0.0}
    raw = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for it in items:
        h, jv = it["h"][dim], it["j"][dim]
        if h is None:
            continue
        k = ("tp" if jv else "fn") if h else ("fp" if jv else "tn")
        cm[k] += w[it["stratum"]]
        raw[k] += 1
    tot = sum(cm.values()) or 1.0
    r = {"n": sum(raw.values()), "raw_confusion": raw,
         "human_rate": (cm["tp"] + cm["fn"]) / tot, "judge_rate": (cm["tp"] + cm["fp"]) / tot,
         "agreement": (cm["tp"] + cm["tn"]) / tot,
         "precision": cm["tp"] / (cm["tp"] + cm["fp"]) if cm["tp"] + cm["fp"] else None,
         "recall": cm["tp"] / (cm["tp"] + cm["fn"]) if cm["tp"] + cm["fn"] else None}
    pe = r["human_rate"] * r["judge_rate"] + (1 - r["human_rate"]) * (1 - r["judge_rate"])
    r["kappa"] = (r["agreement"] - pe) / (1 - pe) if pe < 1 else None
    return r


def _bootstrap(items, w, dim, keys=("human_rate", "agreement", "precision", "recall"), n=2000, seed=SEED):
    rng = random.Random(seed)
    strata = {}
    for it in items:
        strata.setdefault(it["stratum"], []).append(it)
    samples = {k: [] for k in keys}
    for _ in range(n):
        res = [rng.choice(v) for v in strata.values() for _ in v]
        st = _weighted_stats(res, w, dim)
        for k in keys:
            if st[k] is not None:
                samples[k].append(st[k])
    ci = {}
    for k, xs in samples.items():
        xs.sort()
        ci[k] = [xs[int(0.025 * len(xs))], xs[int(0.975 * len(xs)) - 1]] if xs else None
    return ci


def score(tag: str, xlsx: str, meta_path: str, out: str | None, annotator: str) -> dict:
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    assert meta["tag"] == tag, f"meta tag {meta['tag']} != {tag}"
    filled = read_filled(xlsx)
    rows = {(k, q["id"]): pq for k, _rec, q, pq in collect(tag)}
    strata_n = {"flagged": 0, "random": 0}
    for v in meta["items"].values():
        strata_n[v["stratum"]] += 1
    # 层权重：flagged 层代表 judge 标记池，random 层代表未标记池
    w = {"flagged": meta["n_flagged_total"] / strata_n["flagged"], "random": meta["n_ok_total"] / strata_n["random"]}
    items, disagreements, distractor = [], [], []
    for uid, m in sorted(meta["items"].items()):
        if uid not in filled:
            continue
        pq = rows[(m["cache_key"], m["qid"])]
        h, j = human_labels(filled[uid]), judge_labels(pq)
        items.append({"uid": uid, "stratum": m["stratum"], "h": h, "j": j})
        hd = filled[uid]["distractor"]
        if pq["type"] != "judge" and hd not in (None, ""):
            distractor.append((int(hd), int((pq.get("judge") or {}).get("distractor_quality") or 0)))
        diff = [d for d in DIMS[1:] if h[d] is not None and h[d] != j[d]]
        if diff or (h["bad"] is not None and h["bad"] != j["bad"]):
            disagreements.append({"uid": uid, "stratum": m["stratum"], "dims": diff,
                                  "human_bad": h["bad"], "pipeline_flag": j["bad"],
                                  "solver_agrees": pq["solver_agrees"],
                                  "judge_issue": (pq.get("judge") or {}).get("issue", ""),
                                  "human_note": filled[uid]["note"]})
    report = {
        "tag": tag, "judge_version": meta["judge_version"], "annotator": annotator,
        "n_items": len(items), "strata_n": strata_n, "strata_weight": w,
        "note": "按 flagged/random 分层权重还原到全部 baseline 题；human_rate 为人工判定的总体问题率估计",
        "dims": {d: {**_weighted_stats(items, w, d), "ci95": _bootstrap(items, w, d)} for d in DIMS},
        "by_stratum": {s: {d: _weighted_stats([i for i in items if i["stratum"] == s], w, d) for d in DIMS}
                       for s in strata_n},
        "distractor": {
            "n": len(distractor),
            "exact_agree": sum(a == b for a, b in distractor) / len(distractor) if distractor else None,
            "mae": sum(abs(a - b) for a, b in distractor) / len(distractor) if distractor else None,
            "human_mean": sum(a for a, _ in distractor) / len(distractor) if distractor else None,
            "judge_mean": sum(b for _, b in distractor) / len(distractor) if distractor else None,
        },
        "disagreements": disagreements,
    }
    out = out or str(RESULTS / tag / f"calibration_{meta['judge_version']}.json")
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("disagreements", "by_stratum")},
                     ensure_ascii=False, indent=1))
    print(f"wrote {out}; disagreements={len(disagreements)}")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "score"])
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out")
    ap.add_argument("--xlsx", help="score: 回收的标注表")
    ap.add_argument("--meta", help="score: make 生成的 .meta.json")
    ap.add_argument("--annotator", default="human", help="score: 标注来源说明，写入报告")
    a = ap.parse_args()
    if a.cmd == "make":
        make(a.tag, a.out)
    else:
        score(a.tag, a.xlsx, a.meta, a.out, a.annotator)
