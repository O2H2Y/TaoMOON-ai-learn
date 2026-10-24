"""确定性分层切分 dev / holdout：按类别内 sha256(id) 排序，每类约 30% 进 holdout。

切分结果写入 datasets/<name>.split.json 并入库；切分一旦生成就冻结，不随数据集追加而重排
（新增条目只追加分配，已有条目不动）。holdout 只用于最终验收，RSI 优化器不可读取。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
HOLDOUT_RATIO = 0.3


def load(name: str) -> list[dict]:
    return [json.loads(l) for l in (HERE / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def get_split(name: str) -> dict[str, str]:
    path = HERE / f"{name}.split.json"
    existing: dict[str, str] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    items = load(name)
    by_cat: dict[str, list[str]] = defaultdict(list)
    for it in items:
        if it["id"] not in existing:
            by_cat[it["category"]].append(it["id"])
    for cat, ids in by_cat.items():
        ids.sort(key=lambda i: hashlib.sha256(i.encode()).hexdigest())
        n_hold = math.ceil(len(ids) * HOLDOUT_RATIO) if len(ids) >= 3 else 0
        for i, item_id in enumerate(ids):
            existing[item_id] = "holdout" if i < n_hold else "dev"
    path.write_text(json.dumps(dict(sorted(existing.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    return existing


if __name__ == "__main__":  # pragma: no cover
    s = get_split("topics_web_v1")
    print({k: list(s.values()).count(k) for k in ("dev", "holdout")})
