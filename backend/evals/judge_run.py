"""对某次运行（tag）的已生成题目跑异源评审，按 cache_key + JUDGE_VERSION 缓存。

用法：python -m evals.judge_run --tag baseline-2026-10-24 --budget 15 --concurrency 6 [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from evals.scorers.judge import JUDGE_VERSION, judge_quiz

RESULTS = Path(__file__).parent / "results"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--budget", type=float, required=True)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    man = json.loads((RESULTS / args.tag / "manifest.json").read_text(encoding="utf-8"))
    out_dir = RESULTS / "judge" / JUDGE_VERSION
    out_dir.mkdir(parents=True, exist_ok=True)
    keys = man["cache_keys"][: args.limit or None]
    spent, done, stop = 0.0, 0, False
    sem = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()

    async with httpx.AsyncClient() as client:
        async def one(k: str):
            nonlocal spent, done, stop
            path = out_dir / f"{k}.json"
            rec = json.loads((RESULTS / "runs" / f"{k}.json").read_text(encoding="utf-8"))
            if path.exists() or not rec["response"]:
                return
            async with sem:
                if stop:
                    return
                try:
                    res = await judge_quiz(client, rec)
                except Exception as e:
                    print(f"ERR {k} {type(e).__name__}: {str(e)[:200]}", flush=True)
                    return
                path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
                async with lock:
                    spent += res["cost_cny"]
                    done += 1
                    stop = spent >= args.budget
                    print(f"[{done}] {rec['item']['id']}#{rec['sample']} ¥{res['cost_cny']:.4f} total ¥{spent:.3f}", flush=True)

        await asyncio.gather(*(one(k) for k in keys))
    print(json.dumps({"judged_new": done, "cost_cny": round(spent, 4), "stopped_by_budget": stop}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
