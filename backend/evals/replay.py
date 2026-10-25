"""出题环节回放评测：复用某次基线里已保存的搜索资料，只重跑“出题”这一步。

用途：改出题链（结构化输出、重试、提示词）时，排除搜索的随机性，且不消耗 Tavily 额度。
    python -m evals.replay --base baseline-2026-10-24 --split dev --budget 3 --tag structured-v1
结果：evals/results/replay/<tag>/runs.jsonl 与 summary.json（与基线同输入逐局对比）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from app.core.config import get_settings
from app.observability import start_run
from evals.datasets.split import get_split
from evals.pricing import PRICE_SOURCE, cost_cny
from evals.scorers.rules import score_quiz

RESULTS = Path(__file__).parent / "results"


def _quiz_stage(rec: dict) -> tuple[int, int, int]:
    ins = sum(u["input_tokens"] for u in rec["usage"] if u["stage"] == "quiz_generate")
    outs = sum(u["output_tokens"] for u in rec["usage"] if u["stage"] == "quiz_generate")
    ms = sum(int(s["elapsed_ms"]) for s in rec["stages"] if s["name"] == "quiz_generate")
    return ins, outs, ms


def _web_summary(rec: dict) -> str:
    for c in rec.get("contexts", []):
        if c.get("source") == "web" and c.get("status") == "ok":
            return c.get("summary", "")
    return ""


async def replay_one(base: dict) -> dict:
    from app.llm.quiz_chain import generate_quiz

    settings = get_settings()
    it = base["item"]
    t0 = time.time()
    m0 = time.monotonic()
    questions, error = None, None
    with start_run("quiz", harness="replay", item_id=it["id"], sample=base["sample"]) as run:
        try:
            quiz = await generate_quiz(
                user_input=it["user_input"],
                question_count=it["question_count"],
                difficulty=it["difficulty"],
                search_context=_web_summary(base["record"]),
            )
            questions = [q.model_dump() for q in quiz.questions]
        except Exception as e:
            error = f"{type(e).__name__}: {e}"[:500]
    ms = round((time.monotonic() - m0) * 1000)
    tok_in = sum(u.input_tokens for u in run.usage)
    tok_out = sum(u.output_tokens for u in run.usage)
    b_in, b_out, b_ms = _quiz_stage(base["record"])
    b_questions = (base.get("response") or {}).get("questions")
    return {
        "item_id": it["id"], "sample": base["sample"], "category": it.get("category"),
        "new": {"rules": score_quiz(it, questions), "ms": ms, "in": tok_in, "out": tok_out,
                "attempts": run.metadata.get("quiz_generate_attempts"), "error": error,
                "cost": cost_cny(settings.deepseek_model, tok_in, tok_out, t0)},
        "base": {"rules": score_quiz(it, b_questions), "ms": b_ms, "in": b_in, "out": b_out},
        "questions": questions,
    }


def _rate(rows, side, key):
    vals = [bool(r[side]["rules"].get(key)) for r in rows]
    return round(sum(vals) / len(vals), 4) if vals else None


def summarize(rows: list[dict], meta: dict) -> dict:
    def med(side, k):
        return statistics.median(r[side][k] for r in rows if r[side][k] is not None)

    viol = {"new": {}, "base": {}}
    for r in rows:
        for side in ("new", "base"):
            for v in r[side]["rules"].get("violations", []) or []:
                viol[side][v] = viol[side].get(v, 0) + 1
    return {
        **meta,
        "n": len(rows),
        "generated": {"new": _rate(rows, "new", "generated"), "base": _rate(rows, "base", "generated")},
        "format_ok": {"new": _rate(rows, "new", "format_ok"), "base": _rate(rows, "base", "format_ok")},
        "count_ok": {"new": _rate(rows, "new", "count_ok"), "base": _rate(rows, "base", "count_ok")},
        "violations": viol,
        "retried_runs": sum(1 for r in rows if (r["new"]["attempts"] or 1) > 1),
        "failed_runs": [f"{r['item_id']}#{r['sample']}: {r['new']['error']}" for r in rows if r["new"]["error"]],
        "quiz_stage_input_tokens_median": {"new": med("new", "in"), "base": med("base", "in")},
        "quiz_stage_output_tokens_median": {"new": med("new", "out"), "base": med("base", "out")},
        "quiz_stage_ms_median": {"new": med("new", "ms"), "base": med("base", "ms")},
        "cost_cny": round(sum(r["new"]["cost"] for r in rows), 4),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    ap.add_argument("--budget", type=float, required=True)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()

    manifest = json.loads((RESULTS / args.base / "manifest.json").read_text())
    split = get_split(manifest["dataset"])
    bases = []
    for k in manifest["cache_keys"]:
        b = json.loads((RESULTS / "runs" / f"{k}.json").read_text())
        if args.split == "all" or split[b["item"]["id"]] == args.split:
            bases.append(b)
    bases.sort(key=lambda b: (b["item"]["id"], b["sample"]))

    out_dir = RESULTS / "replay" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(args.concurrency)
    rows: list[dict] = []
    spent = 0.0

    async def worker(b):
        nonlocal spent
        async with sem:
            if spent >= args.budget:
                return
            r = await replay_one(b)
            spent += r["new"]["cost"]
            rows.append(r)
            print(f"[{len(rows)}/{len(bases)}] {r['item_id']}#{r['sample']} ok={r['new']['rules'].get('format_ok')} "
                  f"att={r['new']['attempts']} {r['new']['ms']}ms ¥{spent:.3f}", flush=True)

    await asyncio.gather(*(worker(b) for b in bases))
    rows.sort(key=lambda r: (r["item_id"], r["sample"]))
    (out_dir / "runs.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    import subprocess
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    s = summarize(rows, {"tag": args.tag, "base": args.base, "split": args.split, "model": get_settings().deepseek_model,
                         "git_sha": sha, "price_source": PRICE_SOURCE, "stopped_by_budget": len(rows) < len(bases)})
    (out_dir / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    from app.observability.tracing import flush_tracing

    try:
        asyncio.run(main())
    finally:
        flush_tracing()
