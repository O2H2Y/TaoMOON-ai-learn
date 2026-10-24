"""P0 冒烟脚本：跑一次真实出题，把完整 RunRecord（证据、用量、分阶段耗时、原始输出）落盘。

用途：验证 P0 观测链路在真实 API 下工作正常；P1 的评测 runner 会在此基础上批量运行。
不写数据库（user_id=None），不生成配图。

用法（backend 目录下，需在 .env 中配置 DEEPSEEK_API_KEY，联网出题还需 TAVILY_API_KEY）：
    python -m evals.run_once "什么是 RAG" --count 5 --difficulty mixed
输出：evals/results/smoke/<run_id>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path

from app.models.quiz import QuizGenerateRequest
from app.observability import start_run
from app.prompts.registry import PROMPT_VERSIONS, current_fingerprints

OUT_DIR = Path(__file__).parent / "results" / "smoke"


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("user_input")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--difficulty", default="mixed")
    args = parser.parse_args()

    from app.services.quiz_service import handle_quiz_generate

    req = QuizGenerateRequest(
        user_input=args.user_input, question_count=args.count, difficulty=args.difficulty, generate_images=False
    )
    error = None
    response = None
    with start_run("quiz", harness="smoke", git_sha=_git_sha()) as run:
        try:
            response = await handle_quiz_generate(req, user_id=None)
        except Exception as e:  # 失败同样落盘，便于归因
            error = f"{type(e).__name__}: {e}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{run.run_id}.json"
    payload = {
        "request": req.model_dump(),
        "prompt_versions": PROMPT_VERSIONS,
        "prompt_fingerprints": current_fingerprints(),
        "record": run.to_dict(),
        "summary": run.summary(),
        "response": response.model_dump() if response else None,
        "error": error,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(run.summary(), ensure_ascii=False, indent=2))
    print(f"saved -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
