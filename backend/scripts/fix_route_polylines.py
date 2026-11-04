# author: szw
"""整理历史行程里的路线折线（polyline）。

历史数据有两类问题，脚本都能处理：

1. **缺折线**：旧版 parse_route_result 读的是 path["polyline"]，但高德 v3 驾车
   接口的折线只在 steps[].polyline 里，path 级根本没有这个字段 —— 于是每段路线
   的 polyline 都是空的，前端只能退化成起终点之间的一条虚线。
2. **折线没抽稀**：抽稀是后加的，之前存进库的是高德原始折线，一条市区路线
   200~400 点，单条消息能到 50KB。同一个会话刷新几次就把体积堆上去了。

第 2 类只要在本地跑一遍 Douglas-Peucker，不需要联网；第 1 类得重新向高德要，
所以要显式加 --fetch 才做。

用法（在 backend 目录下）：
    python scripts/fix_route_polylines.py                  # 只报告，不写库
    python scripts/fix_route_polylines.py --apply          # 抽稀并写回
    python scripts/fix_route_polylines.py --apply --fetch  # 顺便补全缺失的折线
    python scripts/fix_route_polylines.py --limit 2        # 只看前 2 条消息

写库前会自动把 sessions.db 备份成 sessions.db.bak-<时间戳>。
"""
import argparse
import asyncio
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 这个脚本自己打进度，库里的 INFO 日志会把输出冲散
logging.disable(logging.WARNING)

from app.config import settings
from app.services.amap import driving_route_rest
from app.services.database import db
from app.services.polyline import max_deviation, simplify_polyline

# 省不到 10% 就不改写：已抽稀过的折线再跑一遍几乎不变，
# 没有这条阈值就会把每条消息都重写一次，白白制造 diff。
_MIN_GAIN = 0.10


def _points(route: Dict[str, Any]) -> List:
    polyline = route.get("polyline")
    return polyline if isinstance(polyline, list) else []


def _coord_of(route: Dict[str, Any], key: str):
    coord = route.get(key)
    if isinstance(coord, (list, tuple)) and len(coord) >= 2:
        try:
            return {"lng": float(coord[0]), "lat": float(coord[1])}
        except (TypeError, ValueError):
            return None
    return None


def plan_changes(md: Dict[str, Any], tolerance: float,
                 max_points: int) -> Dict[str, Any]:
    """算出这份 map_data 要做什么，不改动原对象。

    返回 {"missing": [路线下标], "shrink": [(下标, 原点数, 新点数, 新折线)]}
    """
    missing: List[int] = []
    shrink: List[Tuple[int, int, int, List]] = []

    for idx, route in enumerate(md.get("routes") or []):
        points = _points(route)
        if len(points) < 2:
            missing.append(idx)
            continue
        new_points = simplify_polyline(
            points, tolerance_m=tolerance, max_points=max_points
        )
        if len(new_points) < len(points) * (1 - _MIN_GAIN):
            shrink.append((idx, len(points), len(new_points), new_points))

    return {"missing": missing, "shrink": shrink}


def json_size(md: Dict[str, Any]) -> int:
    return len(json.dumps(md, ensure_ascii=False).encode("utf-8"))


async def fetch_missing(md: Dict[str, Any], indexes: List[int]) -> int:
    """给缺折线的路线重新要一次。返回补上的段数。

    逐段串行 + 小睡：高德个人 Key 的 QPS 很低，连发会撞 10021。
    """
    fixed = 0
    routes = md.get("routes") or []
    for idx in indexes:
        route = routes[idx]
        origin = _coord_of(route, "from_coord")
        dest = _coord_of(route, "to_coord")
        if not origin or not dest:
            print(f"   ⏭️ 第 {idx} 段缺端点坐标，跳过：{route.get('from')} → {route.get('to')}")
            continue
        print(f"   🚗 请求折线 {route.get('from')} → {route.get('to')} ...",
              end=" ", flush=True)
        info = await driving_route_rest(origin, dest)
        await asyncio.sleep(0.3)
        if not info or not info.get("polyline"):
            print("拿不到，保持原样")
            continue
        route["polyline"] = info["polyline"]
        if info.get("distance"):
            route["distance"] = info["distance"]
        if info.get("duration"):
            route["duration"] = info["duration"]
        fixed += 1
        print(f"{len(info['polyline'])} 点 / {info['distance']}m / {info['duration']}s")
    return fixed


def backup_db() -> Path:
    src = Path(db.db_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = src.with_name(f"{src.name}.bak-{stamp}")
    shutil.copy2(src, dst)
    return dst


async def main() -> None:
    parser = argparse.ArgumentParser(description="整理历史行程的路线折线")
    parser.add_argument("--apply", action="store_true", help="实际写回数据库")
    parser.add_argument("--fetch", action="store_true",
                        help="对缺折线的路线重新请求高德（会产生 API 调用）")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条消息")
    parser.add_argument("--tolerance", type=float, default=settings.map_polyline_tolerance_m,
                        help=f"抽稀容差（米），默认 {settings.map_polyline_tolerance_m}")
    parser.add_argument("--max-points", type=int,
                        default=settings.map_polyline_max_points,
                        help=f"单段点数上限，默认 {settings.map_polyline_max_points}")
    args = parser.parse_args()

    if args.fetch and not settings.amap_api_key:
        print("❌ 加了 --fetch 但没有配置 AMAP_API_KEY，退出")
        return

    conn = db._get_connection()
    rows = conn.execute(
        "SELECT id, thread_id, map_data FROM messages "
        "WHERE map_data IS NOT NULL AND map_data != '' ORDER BY id"
    ).fetchall()
    conn.close()

    if args.limit:
        rows = rows[:args.limit]

    print(f"带地图数据的消息 {len(rows)} 条，"
          f"容差 {args.tolerance}m，上限 {args.max_points} 点\n")

    before_total = after_total = 0
    touched = 0
    pending_write: List[Tuple[int, str]] = []

    for row in rows:
        msg_id = row["id"]
        try:
            md = json.loads(row["map_data"])
        except (TypeError, ValueError) as e:
            print(f"msg {msg_id} 解析失败，跳过：{e}")
            continue

        size_before = json_size(md)
        before_total += size_before
        plan = plan_changes(md, args.tolerance, args.max_points)
        used = plan["shrink"]
        missing = plan["missing"]

        if missing and args.fetch:
            print(f"--- msg {msg_id}：{len(missing)} 段缺折线 ---")
            await fetch_missing(md, missing)

        if not used and not (missing and args.fetch):
            after_total += json_size(md)
            print(f"msg {msg_id:5} 无需处理")
            continue

        worst = 0.0
        for idx, old_n, new_n, new_points in used:
            # 抽稀前后偏差：脚本自检用，确保"形状没变"不是嘴上说说
            worst = max(worst, max_deviation(_points(md["routes"][idx]), new_points))
            md["routes"][idx]["polyline"] = new_points

        after = json_size(md)
        after_total += after
        touched += 1
        dropped = sum(o - n for _, o, n, _ in used)
        print(f"msg {msg_id:5} 抽稀 {len(used)} 段，点数 {dropped} 个被删，"
              f"最大偏移 {worst:.1f}m，体积 {size_before}B → {after}B")

        if args.apply:
            pending_write.append((msg_id, json.dumps(md, ensure_ascii=False)))

    if args.apply and pending_write:
        bak = backup_db()
        print(f"\n已备份数据库 → {bak.name}")
        conn = db._get_connection()
        for msg_id, payload in pending_write:
            conn.execute("UPDATE messages SET map_data = ? WHERE id = ?", (payload, msg_id))
        conn.commit()
        conn.close()

    saved = before_total - after_total
    pct = saved / before_total if before_total else 0
    print(f"\n{'✅ 已写回' if args.apply else '待处理'} {touched} 条 / 共 {len(rows)} 条")
    print(f"体积 {before_total / 1024:.1f}KB → {after_total / 1024:.1f}KB"
          f"（省 {saved / 1024:.1f}KB，{pct:.0%}）")
    if not args.apply and touched:
        print("加 --apply 生效（写库前会自动备份）")


asyncio.run(main())
