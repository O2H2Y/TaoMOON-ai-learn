"""聚合某次运行（manifest）的逐局结果，输出规则类指标与 95% CI（按主题聚类 bootstrap）。

用法：python -m evals.report --tag baseline-2026-10-24 [--split dev|holdout|all]
输出：evals/results/<tag>/report.json 与 report.md
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from evals.datasets.split import get_split
from evals.scorers.judge import JUDGE_VERSION
from evals.scorers.rules import score_quiz

from app.observability.run_context import is_tool_error

RESULTS = Path(__file__).parent / "results"


def context_diag(rec: dict) -> dict:
    ctxs = rec["record"]["contexts"]
    if not ctxs:
        return {"status": "none", "n_tool_calls": 0, "tool_errors": 0, "no_tool": True, "all_tools_failed": False}
    c = ctxs[0]
    ev = c.get("evidence", [])
    errs = sum(1 for e in ev if e.get("is_error", is_tool_error(e.get("content", ""))))
    return {
        "status": c["status"],
        "n_tool_calls": len(c.get("tool_calls", [])),
        "tool_errors": errs,
        "no_tool": len(c.get("tool_calls", [])) == 0,
        "all_tools_failed": bool(ev) and errs == len(ev),
        "truncated": c.get("truncated", False),
        "raw_len": c.get("raw_summary_length", 0),
        "search_ms": c.get("elapsed_ms", 0),
    }


def cluster_bootstrap(values_by_topic: dict[str, list[float]], n: int = 2000, seed: int = 0):
    topics = [t for t, v in values_by_topic.items() if v]
    if not topics:
        return None, None, None
    allv = [x for t in topics for x in values_by_topic[t]]
    mean = sum(allv) / len(allv)
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        sample = [x for t in (rng.choice(topics) for _ in topics) for x in values_by_topic[t]]
        stats.append(sum(sample) / len(sample))
    stats.sort()
    return mean, stats[int(0.025 * n)], stats[int(0.975 * n) - 1]



def quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * q
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    args = ap.parse_args()

    man = json.loads((RESULTS / args.tag / "manifest.json").read_text(encoding="utf-8"))
    split = get_split(man["dataset"])
    recs = []
    for k in man["cache_keys"]:
        r = json.loads((RESULTS / "runs" / f"{k}.json").read_text(encoding="utf-8"))
        if args.split == "all" or split[r["item"]["id"]] == args.split:
            recs.append(r)

    per_topic: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    viol = Counter()
    q_total = q_bad = 0
    single_longest, single_pos = [], Counter()
    wall, cost, tin, tout, search_ms, gen_ms = [], [], [], [], [], []
    by_cat = defaultdict(lambda: defaultdict(list))
    errors = Counter()

    for r in recs:
        t = r["item"]["id"]
        cat = r["item"]["category"]
        qs = r["response"]["questions"] if r["response"] else None
        s = score_quiz(r["item"], qs)
        d = context_diag(r)
        m = per_topic[t]
        m["success"].append(float(s["generated"]))
        if r["error"]:
            errors[r["error"].split(":")[0]] += 1
        m["search_failed"].append(float(d["status"] != "ok" or d["all_tools_failed"] or d["no_tool"]))
        m["search_all_tools_failed_but_ok"].append(float(d["status"] == "ok" and d["all_tools_failed"]))
        m["context_truncated"].append(float(d.get("truncated", False)))
        jp = RESULTS / "judge" / JUDGE_VERSION / f"{r['cache_key']}.json"
        if s["generated"] and jp.exists():
            jd = json.loads(jp.read_text(encoding="utf-8"))
            for pq in jd["per_question"]:
                j = pq.get("judge") or {}
                m["j_solver_agrees"].append(float(pq["solver_agrees"]))
                if j:
                    m["j_answer_correct"].append(float(j.get("answer_correct") == "yes"))
                    m["j_answer_wrong"].append(float(j.get("answer_correct") == "no"))
                    m["j_unique_answer"].append(float(bool(j.get("unique_answer"))))
                    m["j_explanation_faithful"].append(float(bool(j.get("explanation_faithful"))))
                    m["j_hallucination"].append(float(bool(j.get("hallucination"))))
                    m["j_on_topic"].append(float(bool(j.get("on_topic"))))
                    if pq["type"] != "judge":
                        m["j_distractor_q"].append(float(j.get("distractor_quality") or 0))
                    good = (j.get("answer_correct") == "yes" and j.get("unique_answer") and j.get("explanation_faithful")
                            and not j.get("hallucination") and j.get("on_topic") and pq["solver_agrees"])
                    m["j_question_good"].append(float(bool(good)))
                    by_cat[cat]["j_question_good"].append(float(bool(good)))
        if s["generated"]:
            for key in ("format_ok", "count_ok", "type_mix_ok"):
                m[key].append(float(s[key]))
            m["bad_question_rate"].append(s["n_bad_questions"] / max(1, s["n_questions"]))
            m["has_intra_dup"].append(float(s["intra_dup_pairs"] > 0))
            for vs in s["question_violations"]:
                q_total += 1
                q_bad += bool(vs)
                viol.update(vs)
            single_longest += s["single_answer_is_longest"]
            single_pos.update(s["single_answer_positions"])
            by_cat[cat]["format_ok"].append(float(s["format_ok"]))
        by_cat[cat]["success"].append(float(s["generated"]))
        wall.append(r["wall_ms"])
        cost.append(r["cost_cny"])
        tin.append(r["tokens"]["input_tokens"])
        tout.append(r["tokens"]["output_tokens"])
        st = {}
        for x in r["record"]["stages"]:
            st[x["name"]] = st.get(x["name"], 0) + x["elapsed_ms"]
        if "web_search" in st:
            search_ms.append(st["web_search"])
        if "quiz_generate" in st:
            gen_ms.append(st["quiz_generate"])

    metrics = {}
    for key in ("success", "format_ok", "count_ok", "type_mix_ok", "bad_question_rate", "has_intra_dup",
                "search_failed", "search_all_tools_failed_but_ok", "context_truncated",
                "j_solver_agrees", "j_answer_correct", "j_answer_wrong", "j_unique_answer", "j_explanation_faithful",
                "j_hallucination", "j_on_topic", "j_distractor_q", "j_question_good"):
        vbt = {t: v[key] for t, v in per_topic.items()}
        mean, lo, hi = cluster_bootstrap(vbt)
        metrics[key] = {"mean": mean, "ci95": [lo, hi], "n": sum(len(v) for v in vbt.values())}

    n_single = len(single_longest)
    report = {
        "tag": args.tag,
        "split": args.split,
        "dataset": man["dataset"],
        "git_sha": man["git_sha"],
        "model": man["model"],
        "prompt_versions": man["prompt_versions"],
        "n_runs": len(recs),
        "n_topics": len(per_topic),
        "metrics": metrics,
        "question_level": {
            "n_questions": q_total,
            "bad_question_rate": q_bad / max(1, q_total),
            "violations": dict(viol.most_common()),
        },
        "bias": {
            "single_answer_is_unique_longest_rate": sum(single_longest) / max(1, n_single),
            "n_single": n_single,
            "single_answer_position_dist": {k: v / max(1, sum(single_pos.values())) for k, v in sorted(single_pos.items())},
        },
        "errors": dict(errors),
        "latency_ms": {
            "wall_p50": quantile(wall, 0.5), "wall_p95": quantile(wall, 0.95),
            "search_p50": quantile(search_ms, 0.5), "search_p95": quantile(search_ms, 0.95),
            "generate_p50": quantile(gen_ms, 0.5), "generate_p95": quantile(gen_ms, 0.95),
        },
        "cost": {
            "per_run_mean_cny": statistics.mean(cost) if cost else 0,
            "input_tokens_mean": statistics.mean(tin) if tin else 0,
            "output_tokens_mean": statistics.mean(tout) if tout else 0,
            "note": "按缓存未命中计价，为上界；价格来源见 evals/pricing.py",
        },
        "by_category": {c: {k: sum(v) / len(v) for k, v in d.items()} for c, d in sorted(by_cat.items())},
    }
    out = RESULTS / args.tag
    (out / f"report_{args.split}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
