"""离线评测 runner：批量真实出题，逐局落盘完整 RunRecord，支持缓存、并发与预算上限。

用法（backend 目录）：
    python -m evals.runner --dataset topics_web_v1 --split all --samples 3 --budget 20 --concurrency 6 --tag baseline
产物：
    evals/results/runs/<cache_key>.json   每局一份（相同输入+prompt 指纹+模型+样本序号命中缓存，不重复花钱）
    evals/results/<tag>/manifest.json      本次运行涉及的 cache_key 列表与配置快照
缓存键刻意不含 git sha：只改评分代码时可复用生成结果；改了 prompt（指纹变化）或模型会自动失效。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import time
import traceback
from pathlib import Path

from app.core.config import get_settings
from app.models.quiz import QuizGenerateRequest
from app.observability import start_run
from app.prompts.registry import PROMPT_VERSIONS, current_fingerprints
from evals.datasets.split import get_split, load
from evals.pricing import PRICE_SOURCE, cost_cny

RESULTS = Path(__file__).parent / "results"
RUNS = RESULTS / "runs"


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def cache_key(item: dict, sample: int, model: str, fingerprints: dict) -> str:
    payload = json.dumps(
        {
            "input": item["user_input"],
            "count": item["question_count"],
            "difficulty": item["difficulty"],
            "doc_id": item.get("doc_id"),
            "sample": sample,
            "model": model,
            "fp": fingerprints,
            "web": get_settings().enable_web_search,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


async def run_one(item: dict, sample: int, key: str, sha: str) -> dict:
    from app.services.quiz_service import handle_quiz_generate

    settings = get_settings()
    req = QuizGenerateRequest(
        user_input=item["user_input"],
        question_count=item["question_count"],
        difficulty=item["difficulty"],
        doc_id=item.get("doc_id"),
        generate_images=False,
    )
    t0 = time.time()
    m0 = time.monotonic()
    response, error = None, None
    with start_run("quiz", harness="eval", item_id=item["id"], sample=sample, git_sha=sha) as run:
        try:
            response = await handle_quiz_generate(req, user_id=None)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"[:2000]
            run.raw_outputs.setdefault("traceback", traceback.format_exc()[-4000:])
    wall_ms = round((time.monotonic() - m0) * 1000)
    tokens = run.total_tokens()
    cost = sum(cost_cny(u.model or settings.deepseek_model, u.input_tokens, u.output_tokens, t0) for u in run.usage)
    out = {
        "cache_key": key,
        "item": item,
        "sample": sample,
        "model": settings.deepseek_model,
        "prompt_versions": PROMPT_VERSIONS,
        "prompt_fingerprints": current_fingerprints(),
        "git_sha": sha,
        "started_at": t0,
        "wall_ms": wall_ms,
        "cost_cny": cost,
        "tokens": tokens,
        "record": run.to_dict(),
        "response": response.model_dump() if response else None,
        "error": error,
    }
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{key}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="topics_web_v1")
    ap.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--budget", type=float, required=True, help="本次运行新增花费上限（元），超出即停止派发")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个条目（调试用）")
    args = ap.parse_args()

    settings = get_settings()
    split = get_split(args.dataset)
    items = [it for it in load(args.dataset) if args.split == "all" or split[it["id"]] == args.split]
    if args.limit:
        items = items[: args.limit]
    fps = current_fingerprints()
    sha = git_sha()

    jobs = []
    for it in items:
        for s in range(args.samples):
            jobs.append((it, s, cache_key(it, s, settings.deepseek_model, fps)))

    spent = 0.0
    done = cached = failed = 0
    stop = False
    sem = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()

    async def worker(it, s, key):
        nonlocal spent, done, cached, failed, stop
        if (RUNS / f"{key}.json").exists():
            cached += 1
            return
        async with sem:
            if stop:
                return
            res = await run_one(it, s, key, sha)
            async with lock:
                spent += res["cost_cny"]
                done += 1
                failed += res["error"] is not None
                if spent >= args.budget:
                    stop = True
                print(f"[{done + cached}/{len(jobs)}] {it['id']}#{s} {res['wall_ms']}ms "
                      f"¥{res['cost_cny']:.4f} total ¥{spent:.3f} {'ERR ' + res['error'][:80] if res['error'] else ''}",
                      flush=True)

    await asyncio.gather(*(worker(*j) for j in jobs))

    out_dir = RESULTS / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "tag": args.tag,
        "dataset": args.dataset,
        "split": args.split,
        "samples": args.samples,
        "model": settings.deepseek_model,
        "web_search": settings.enable_web_search,
        "prompt_versions": PROMPT_VERSIONS,
        "prompt_fingerprints": fps,
        "git_sha": sha,
        "price_source": PRICE_SOURCE,
        "new_runs": done,
        "cached_runs": cached,
        "failed_new_runs": failed,
        "new_cost_cny_upper_bound": round(spent, 4),
        "stopped_by_budget": stop,
        "cache_keys": [k for _, _, k in jobs if (RUNS / f"{k}.json").exists()],
        "finished_at": time.time(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "cache_keys"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    from app.observability.tracing import flush_tracing

    try:
        asyncio.run(main())
    finally:
        flush_tracing()
