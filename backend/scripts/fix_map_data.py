# author: szw
"""
一次性修复历史行程里画错的景点坐标。

背景：旧版 generate_map_data 不校验高德地理编码的 level，当高德查不到具体 POI 时
会静默返回"城市中心点"，于是多个景点被画到同一个坐标上，地图上只看得见最后一个。
典型现象是三亚行程第三天「椰梦长廊 → 三亚免税城」两个点完全重合。

处理范围：只重算"存在重复坐标"的消息，其他地图数据一律不动，
避免把本来正确的大连行程也重算一遍（万一某次查询失败反而变差）。

用法（在 backend 目录下）：
    python scripts/fix_map_data.py            # 只报告，不写库
    python scripts/fix_map_data.py --apply    # 实际写库
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


def extract_city(destination: str) -> str:
    """从 '海南省三亚市' 这类目的地里取出 '三亚'"""
    city = destination or ""
    if "省" in city:
        parts = city.split("省")
        if len(parts) > 1:
            city = parts[1]
    if "市" in city:
        city = city.split("市")[0]
    return city.strip()


def has_duplicate_coords(attractions) -> bool:
    coords = [
        (round(float(a.get("lng", 0)), 5), round(float(a.get("lat", 0)), 5))
        for a in attractions
        if a.get("lng") is not None and a.get("lat") is not None
    ]
    return len(coords) != len(set(coords))


async def fix_one(geo_tool, msg_id: int, md: dict, apply: bool) -> bool:
    """重算一条消息的 map_data，返回是否需要更新"""
    attractions = md.get("attractions", [])
    if not has_duplicate_coords(attractions):
        return False

    city = extract_city(md.get("destination", ""))
    print(f"\n--- msg {msg_id} | 目的地 {md.get('destination')!r} -> city={city!r} ---")
    print("   修复前:", [(a["name"], a["lng"], a["lat"]) for a in attractions])

    name2coord = {}
    located = []
    failed = []
    for i, attr in enumerate(attractions):
        coords = await A.geocode_attraction(geo_tool, attr["name"], city, i + 1, len(attractions))
        if coords:
            attr["lng"], attr["lat"] = coords["lng"], coords["lat"]
            attr.pop("approx", None)
            located.append(attr)
            name2coord[attr["name"]] = [coords["lng"], coords["lat"]]
        else:
            failed.append(attr)

    if failed:
        center_base = await A._resolve_center_base(
            located, geo_tool, city, (A.settings.default_map_lng, A.settings.default_map_lat)
        )
        for i, attr in enumerate(failed):
            lng, lat = A._scatter_around(center_base, i, len(failed))
            attr["lng"], attr["lat"], attr["approx"] = lng, lat, True
            name2coord[attr["name"]] = [lng, lat]

    # 路线端点跟着更新，否则前端画出来的线还是连到老坐标
    for route in md.get("routes", []):
        for name_key, coord_key in (("from", "from_coord"), ("to", "to_coord")):
            name = route.get(name_key)
            if name in name2coord:
                route[coord_key] = name2coord[name]

    # 中心点按精确坐标重算，估算点不参与
    precise = [a for a in attractions if not a.get("approx")]
    source = precise or attractions
    if source:
        md["center"] = [
            round(sum(a["lng"] for a in source) / len(source), 6),
            round(sum(a["lat"] for a in source) / len(source), 6),
        ]

    print("   修复后:", [(a["name"], a["lng"], a["lat"], a.get("approx", False)) for a in attractions])
    print("   仍有重复坐标:", has_duplicate_coords(attractions))

    if apply:
        conn = db._get_connection()
        conn.execute(
            "UPDATE messages SET map_data = ? WHERE id = ?",
            (json.dumps(md, ensure_ascii=False), msg_id),
        )
        conn.commit()
        conn.close()
        print("   ✅ 已写回数据库")
    else:
        print("   （未写库，加 --apply 生效）")
    return True


async def main():
    apply = "--apply" in sys.argv
    await A.ensure_tools()
    geo_tool = A.tools_by_name.get("maps_geo")
    if not geo_tool:
        print("❌ maps_geo 工具不可用，退出")
        return

    conn = db._get_connection()
    rows = conn.execute(
        "SELECT id, thread_id, map_data FROM messages "
        "WHERE map_data IS NOT NULL AND map_data != '' ORDER BY id"
    ).fetchall()
    conn.close()

    print(f"共 {len(rows)} 条带地图数据的消息")
    fixed = 0
    for row in rows:
        try:
            md = json.loads(row["map_data"])
        except Exception as e:
            print(f"msg {row['id']} 解析失败: {e}")
            continue
        if await fix_one(geo_tool, row["id"], md, apply):
            fixed += 1

    print(f"\n{'✅ 已修复' if apply else '待修复'} {fixed} 条 / 共 {len(rows)} 条")


asyncio.run(main())
