# author: szw
"""高德直连自检：确认真实接口能拿到折线，并打印每段的点数。

只用标准库，任何 Python 3.8+ 都能跑，不需要装 httpx / dotenv。

用法：
    python scripts/check_amap_route.py 星海广场 老虎滩海洋公园 --city 大连
    python scripts/check_amap_route.py 星海广场 老虎滩海洋公园 大连站 --city 大连

输出里会明确区分两件事：
    - 汇总层（path.polyline）有没有坐标
    - steps 里拼出来的折线有多少点
之前「地图画不出线」就是只看汇总层导致的，这个脚本用来一眼分辨。
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
PLACE_URL = "https://restapi.amap.com/v3/place/text"
DRIVING_URL = "https://restapi.amap.com/v3/direction/driving"

# 抽稀模块在 app/services 下，纯标准库实现，所以这个脚本仍然免依赖
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.polyline import max_deviation, simplify_polyline  # noqa: E402


def _get(url, params, timeout=8):
    query = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{query}", timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_key(env_path):
    """从 .env 里读 AMAP_API_KEY，避免把 key 写进命令行历史。"""
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("AMAP_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("AMAP_API_KEY", "")


def locate(key, name, city):
    """先按地址查，查不到再按 POI 搜。返回 (lng, lat, 命中名称)。"""
    data = _get(GEOCODE_URL, {"key": key, "address": name, "city": city})
    if str(data.get("status")) == "1" and data.get("geocodes"):
        item = data["geocodes"][0]
        lng, lat = item["location"].split(",")
        return float(lng), float(lat), item.get("formatted_address", name), "geocode"

    data = _get(PLACE_URL, {"key": key, "keywords": name, "city": city, "offset": 1})
    if str(data.get("status")) == "1" and data.get("pois"):
        item = data["pois"][0]
        lng, lat = item["location"].split(",")
        return float(lng), float(lat), item.get("name", name), "place"

    return None


def parse_polyline(text):
    """高德把折线给成 'lng,lat;lng,lat;...' 的文本，切成坐标数组。"""
    points = []
    for chunk in (text or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(",")
        if len(parts) < 2:
            continue
        try:
            points.append([float(parts[0]), float(parts[1])])
        except ValueError:
            continue
    return points


def analyze(payload):
    """把一条驾车路线拆成：汇总层折线 / steps 拼接折线。"""
    route = payload.get("route") or {}
    paths = route.get("paths") or []
    result = {"status": payload.get("status"), "info": payload.get("info"), "paths": len(paths)}
    if not paths:
        return result

    path = paths[0]
    summary_polyline = parse_polyline(path.get("polyline"))
    steps = path.get("steps") or []
    merged = []
    for step in steps:
        merged.extend(parse_polyline(step.get("polyline")))

    # 相邻路段首尾共点，去重后才是真实折线点数
    deduped = []
    for point in merged:
        if not deduped or deduped[-1] != point:
            deduped.append(point)

    result.update({
        "distance": int(path.get("distance") or 0),
        "duration": int(path.get("duration") or 0),
        "summary_points": len(summary_polyline),
        "step_count": len(steps),
        "step_points": len(merged),
        "merged_points": len(deduped),
        "polyline": deduped,
        "first": deduped[0] if deduped else None,
        "last": deduped[-1] if deduped else None,
        "empty_steps": sum(1 for s in steps if not parse_polyline(s.get("polyline"))),
    })
    return result


def fmt_point(point):
    return f"({point[0]:.6f}, {point[1]:.6f})" if point else "无"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser()
    parser.add_argument("places", nargs="+", help="按顺序串联的地点名，至少 2 个")
    parser.add_argument("--city", required=True)
    parser.add_argument("--tolerance", type=float, default=6.0, help="抽稀容差（米），0 表示只按上限采样")
    parser.add_argument("--max-points", type=int, default=400, help="单段折线点数上限")
    parser.add_argument("--env", default=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    args = parser.parse_args()

    if len(args.places) < 2:
        print("至少需要 2 个地点")
        return 2

    key = load_key(os.path.abspath(args.env))
    if not key:
        print("AMAP_API_KEY 未配置（.env 或环境变量）")
        return 2
    print(f"key 已加载（长度 {len(key)}，类型需为「Web服务」）\n")

    coords = []
    for name in args.places:
        hit = locate(key, name, args.city)
        if not hit:
            print(f"✗ 定位失败：{name}")
            return 1
        lng, lat, resolved, source = hit
        coords.append((name, lng, lat))
        print(f"✓ {name} → {fmt_point([lng, lat])}  [{source}] {resolved}")
    print()

    total_distance = total_duration = 0
    all_ok = True
    for i in range(len(coords) - 1):
        src, dst = coords[i], coords[i + 1]
        params = {
            "key": key,
            "origin": f"{src[1]:.6f},{src[2]:.6f}",
            "destination": f"{dst[1]:.6f},{dst[2]:.6f}",
            "extensions": "base",
        }
        try:
            payload = _get(DRIVING_URL, params)
        except Exception as exc:
            print(f"✗ {src[0]} → {dst[0]} 请求异常：{exc}")
            all_ok = False
            continue

        info = analyze(payload)
        print(f"[{i + 1}] {src[0]} → {dst[0]}")
        if str(info.get("status")) != "1" or not info.get("paths"):
            print(f"    ✗ 接口未返回路线：status={info.get('status')} info={info.get('info')}")
            print("      若 info 含 SERVICE_NOT_AVAILABLE / INVALID_USER_KEY，多半是 key 类型不对或配额用尽")
            all_ok = False
            continue

        total_distance += info["distance"]
        total_duration += info["duration"]
        print(f"    距离 {info['distance'] / 1000:.2f} km，预计 {info['duration'] / 60:.0f} 分钟")
        print(f"    汇总层 path.polyline 点数：{info['summary_points']}  ← 这里为空是正常的")
        print(f"    steps 段数：{info['step_count']}（其中无坐标 {info['empty_steps']} 段）")
        print(f"    拼接后折线点数：{info['merged_points']}")
        print(f"    首点 {fmt_point(info['first'])}  末点 {fmt_point(info['last'])}")

        raw = info["polyline"]
        simplified = simplify_polyline(raw, tolerance_m=args.tolerance, max_points=args.max_points)
        deviation = max_deviation(raw, simplified)
        bytes_raw = len(json.dumps(raw, separators=(",", ":")).encode("utf-8"))
        bytes_new = len(json.dumps(simplified, separators=(",", ":")).encode("utf-8"))
        saved = (1 - bytes_new / bytes_raw) * 100 if bytes_raw else 0
        print(f"    抽稀后（容差 {args.tolerance:g}m，上限 {args.max_points}）：{len(simplified)} 点")
        print(f"    实测最大偏移：{deviation:.2f} m")
        print(f"    JSON 体积：{bytes_raw} B → {bytes_new} B（省 {saved:.0f}%）")

        if info["merged_points"] <= 2:
            print("    ✗ 折线点数过少，前端只会画成直线，需检查解析逻辑")
            all_ok = False
        print()

    print(f"合计 {total_distance / 1000:.2f} km / {total_duration / 60:.0f} 分钟")
    print("结论：" + ("全部有折线，可视化正常" if all_ok else "存在异常段，见上方 ✗"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
