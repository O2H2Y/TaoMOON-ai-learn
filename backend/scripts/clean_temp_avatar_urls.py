"""一次性清洗用户表中的设备临时头像地址。

背景：客户端曾把微信 chooseAvatar 返回的临时路径（http://tmp/xxx.jpeg、
wxfile://xxx）直接写入 users.avatar_url，这类地址换设备或清理缓存后必然失效，
并在开发者工具中触发 __tmp__ / CORS 报错。修复后由渲染层兜底，同时用本脚本
把历史脏数据置空，用户重新选择一次头像即可得到持久化地址。

用法（在 backend 目录下）：
    python scripts/clean_temp_avatar_urls.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 允许直接以 `python scripts/clean_temp_avatar_urls.py` 运行：把 backend 根目录加入 import 路径
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.db import close_mysql_pool, get_mysql_pool, init_mysql  # noqa: E402

TEMP_AVATAR_PATTERNS = ("http://tmp/%", "https://tmp/%", "wxfile://%")

COUNT_SQL = (
    "SELECT COUNT(*) FROM users "
    "WHERE avatar_url LIKE %s OR avatar_url LIKE %s OR avatar_url LIKE %s"
)
CLEAN_SQL = (
    "UPDATE users SET avatar_url = '' "
    "WHERE avatar_url LIKE %s OR avatar_url LIKE %s OR avatar_url LIKE %s"
)


async def main() -> None:
    await init_mysql()
    pool = get_mysql_pool()
    if pool is None:
        raise RuntimeError("MySQL 未初始化，无法执行清洗")

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(COUNT_SQL, TEMP_AVATAR_PATTERNS)
            before_row = await cur.fetchone()
            before = int(before_row[0]) if before_row else 0
            print(f"[1/3] 匹配到的临时头像地址数量: {before}")

            updated = 0
            if before:
                await cur.execute(CLEAN_SQL, TEMP_AVATAR_PATTERNS)
                updated = cur.rowcount
            print(f"[2/3] 本次置空的记录数: {updated}")

            await cur.execute(COUNT_SQL, TEMP_AVATAR_PATTERNS)
            after_row = await cur.fetchone()
            after = int(after_row[0]) if after_row else 0
            print(f"[3/3] 清洗后剩余数量: {after}")

    await close_mysql_pool()


if __name__ == "__main__":
    asyncio.run(main())