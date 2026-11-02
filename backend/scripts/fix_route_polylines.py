# author: szw
"""
一次性补齐历史行程里的**路线折线**（polyline）。

背景：旧版 parse_route_result 读的是 path["polyline"]，但高德 v3/direction/driving
的折线只放在 steps[].polyline 里，path 级根本没有这个字段 —— 于是每段路线的 polyline
都是空的，前端只能退化成起终点之间的一条虚线（"地图上只有点，没有路线"）。

代码修好后，只会影响**新生成**的行程；已经存进 sessions.db 的老数据还是空的。
这个脚本就是把老数据里的每段路线重新向高德要一次折线，顺手把 distance/duration 也校正成数字。

用法（在 backend 目录下）：
    python scripts/fix_route_polylines.py                    # 只报告
    python scripts/fix_route_polylines.py --apply            # 实际写库
    python scripts/fix_route_polylines.py --limit 1 --apply  # 只处理 1 条消息
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging

logging.disable(logging.WARNING)

import app.core.agents as A
from app.services.database import db


def needs_fix(md: dict) -> bool:
    """只要有一段路线缺折线（或不足 2 个点）就需要修"""
    for route in md.get("routes") or []:
        polyline = route.get("polyline") or []
        if not isinstance(polyline, list) or len(polyline) < 2:
            return True
    return False


def _coord_of(route: dict, key: str):
    coord = route.get(key)
    if isinstance(coord, (list, tuple)) and len(coord) >= 2:
        try:
            return {"lng": float(coord[0]), "lat": float(coord[1])}
        except (TypeError, ValueError):
            return None
    return None


async def fix_one(msg_id: int, md: dict, apply: bool) -> bool:
    routes = md.get("routes") or []
    if not routes:
        return False

    changed = 0
    for idx, route in enumerate(routes):
        origin, dest = _coord_of(route, "from_coord"), _coord_of(route, "to_coord")
        if not origin or not dest:
            print(f"   ⏭️ 路线 {idx} 缺少端点坐标，跳过: {route.get('from')} → {route.get('to')}")
            continue

        print(f"   🚗 {route.get('from')} → {route.get('to')} ...", end=" ", flush=True)
        info = await A._driving_route_rest(origin, dest)
        # 高德个人 Key 的 QPS 很低，脚本连发容易撞 10021
        await asyncio.sleep(0.3)
        if not info or not info.get("polyline"):
            print("拿不到折线，保持原样")
            continue

        route["polyline"] = info["polyline"]
        if info.get("distance"):
            route["distance"] = info["distance"]
        if info.get("duration"):
            route["duration"] = info["duration"]
        changed += 1
        print(f"{len(info['polyline'])} 个点 / {info['distance']}m / {info['duration']}s")

    if not changed:
        return False

    if apply:
        conn = db._get_connection()
        conn.execute(
            "UPDATE messages SET map_data = ? WHERE id = ?",
            (json.dumps(md, ensure_ascii=False), msg_id),
        )
        conn.commit()
        conn.close()
        print(f"   ✅ msg {msg_id} 已写回 {changed} 段路线")
    else:
        print(f"   （msg {msg_id} 待更新 {changed} 段，加 --apply 生效）")
    return True


async def main():
    apply = "--apply" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except (IndexError, ValueError):
            print("--limit 后面要跟一个数字")
            return

    if not A.settings.amap_api_key:
        print("❌ 没有配置 AMAP_API_KEY，无法请求路线，退出")
        return

    conn = db._get_connection()
    rows = conn.execute(
        "SELECT id, thread_id, map_data FROM messages "
        "WHERE map_data IS NOT NULL AND map_data != '' ORDER BY id"
    ).fetchall()
    conn.close()

    todo = []
    for row in rows:
        try:
            md = json.loads(row["map_data"])
        except Exception as e:
            print(f"msg {row['id']} 解析失败: {e}")
            continue
        if needs_fix(md):
            todo.append((row["id"], md))

    if limit:
        todo = todo[:limit]

    print(f"共 {len(rows)} 条带地图数据的消息，其中 {len(todo)} 条缺折线")
    fixed = 0
    for msg_id, md in todo:
        print(f"\n--- msg {msg_id} ---")
        if await fix_one(msg_id, md, apply):
            fixed += 1

    print(f"\n{'✅ 已修复' if apply else '待修复'} {fixed} 条 / 待处理 {len(todo)} 条")


asyncio.run(main())
