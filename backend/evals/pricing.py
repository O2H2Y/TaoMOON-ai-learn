"""成本估算。单价来源：DeepSeek 官方价格页 https://api-docs.deepseek.com/zh-cn/quick_start/pricing
（2026-10-24 抓取）。旧模型名（deepseek-chat 等）按 Flash 价格计费。
按缓存未命中计价，是上界；高峰时段：北京时间工作日 9:00-12:00、14:00-18:00（未处理法定节假日）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

PRICES_CNY_PER_M = {
    # model: (input_cache_miss_idle, output_idle)
    "deepseek-flash": (1.0, 4.0),
    "deepseek-chat": (1.0, 4.0),
    "deepseek-v4-flash": (1.0, 4.0),
    "deepseek-v4-pro": (4.5, 13.5),
}
PRICE_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing (fetched 2026-10-24)"


def is_peak(ts: float) -> bool:
    bj = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
    if bj.weekday() >= 5:
        return False
    h = bj.hour + bj.minute / 60
    return 9 <= h < 12 or 14 <= h < 18


def cost_cny(model: str, input_tokens: int, output_tokens: int, ts: float) -> float:
    pin, pout = PRICES_CNY_PER_M.get(model, PRICES_CNY_PER_M["deepseek-chat"])
    mult = 2.0 if is_peak(ts) else 1.0
    return (input_tokens * pin + output_tokens * pout) / 1_000_000 * mult
