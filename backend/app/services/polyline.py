# author: szw
"""折线抽稀：把高德返回的几百个坐标点压到几十个，形状不变。

为什么需要：
    高德驾车接口一条市区路线经 steps 拼接后通常 200~400 点，一天 5 段就是
    上千点。这些点会完整进入 SSE 推送和 SQLite 历史，单条消息体积能到
    几十上百 KB，而屏幕上根本看不出区别。

为什么用 Douglas-Peucker 而不是"每隔 N 个取一个"：
    等间隔采样会把直角转弯抹平。城市道路全是直角路口，抹平后线会穿过建筑。
    DP 只删"偏离保留折线不超过容差"的点，拐角处的点因为偏差大必然被保留。

精度说明：
    经纬度先投影到平面（等距圆柱近似）再算距离，城市尺度（几十公里内）
    误差在千分之一量级，对 6 米的容差没有影响。
"""
import math

# 1 度经纬度对应的米数（WGS84 平均值，城市尺度足够）
_M_PER_DEG_LAT = 110574.0
_M_PER_DEG_LNG = 111320.0


def _clean(points):
    """过滤非法点、去掉相邻重复点。非法点直接丢弃，不让它污染几何计算。"""
    out = []
    for point in points or []:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            lng = float(point[0])
            lat = float(point[1])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(lng) and math.isfinite(lat)):
            continue
        if not (-180.0 <= lng <= 180.0 and -90.0 <= lat <= 90.0):
            continue
        current = [lng, lat]
        if not out or out[-1] != current:
            out.append(current)
    return out


def _project(points, ref_lat):
    """经纬度转平面米制坐标。经度按参考纬度缩放，否则高纬度会横向拉长。"""
    scale = math.cos(math.radians(ref_lat))
    if scale <= 1e-6:
        scale = 1e-6
    return [
        (lng * _M_PER_DEG_LNG * scale, lat * _M_PER_DEG_LAT)
        for lng, lat in points
    ]


def _segment_distance(point, start, end):
    """点到线段的距离（米）。线段退化成点时按点距算。"""
    px, py = point
    ax, ay = start
    bx, by = end
    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _douglas_peucker(xy, tolerance):
    """返回需要保留的下标。用显式栈而不是递归，避免上千点时递归深度超限。

    复杂度最坏 O(n^2)，但路线折线是平滑的道路形状，实际远低于这个量级。
    """
    count = len(xy)
    if count <= 2:
        return list(range(count))

    keep = [False] * count
    keep[0] = True
    keep[-1] = True
    stack = [(0, count - 1)]

    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue

        max_distance = -1.0
        max_index = -1
        for index in range(start + 1, end):
            distance = _segment_distance(xy[index], xy[start], xy[end])
            if distance > max_distance:
                max_distance = distance
                max_index = index

        if max_distance > tolerance and max_index > 0:
            keep[max_index] = True
            stack.append((start, max_index))
            stack.append((max_index, end))

    return [index for index, flag in enumerate(keep) if flag]


def _evenly_sample(points, max_points):
    """DP 之后仍然超上限时兜底等间隔取样。只在容差设得极小时才会走到。"""
    count = len(points)
    if max_points < 2 or count <= max_points:
        return points

    step = (count - 1) / (max_points - 1)
    indexes = sorted({int(round(i * step)) for i in range(max_points)})
    sampled = [points[i] for i in indexes]
    if sampled[0] != points[0]:
        sampled.insert(0, points[0])
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def simplify_polyline(points, tolerance_m=6.0, max_points=400):
    """抽稀折线，保留首尾与拐角。

    Args:
        points: [[lng, lat], ...]，允许含脏数据
        tolerance_m: 容差（米）。被删掉的点到保留折线的最大偏离不会超过它，
            这也是"形状没变"的量化依据。0 或负数表示不做几何抽稀。
        max_points: 点数上限，超过时等间隔兜底。None 或 0 表示不限制。

    Returns:
        [[lng, lat], ...]，坐标保留 6 位小数（高德本身也只给 6 位）。
    """
    cleaned = _clean(points)
    if len(cleaned) <= 2:
        return [[round(lng, 6), round(lat, 6)] for lng, lat in cleaned]

    if tolerance_m and tolerance_m > 0:
        ref_lat = sum(lat for _, lat in cleaned) / len(cleaned)
        xy = _project(cleaned, ref_lat)
        kept = _douglas_peucker(xy, float(tolerance_m))
        simplified = [cleaned[i] for i in kept]
    else:
        simplified = cleaned

    if max_points and max_points > 0:
        simplified = _evenly_sample(simplified, int(max_points))

    return [[round(lng, 6), round(lat, 6)] for lng, lat in simplified]


def max_deviation(points, simplified):
    """抽稀前后的最大偏移（米），用于验证"形状真的没变"。

    逐个原始点算它到简化折线的最短距离，取最大值。O(n*m)，
    只给自检脚本用，不参与线上链路。
    """
    original = _clean(points)
    kept = _clean(simplified)
    if len(original) < 2 or len(kept) < 2:
        return 0.0

    ref_lat = sum(lat for _, lat in original) / len(original)
    src = _project(original, ref_lat)
    dst = _project(kept, ref_lat)

    worst = 0.0
    for point in src:
        best = min(
            _segment_distance(point, dst[i], dst[i + 1])
            for i in range(len(dst) - 1)
        )
        if best > worst:
            worst = best
    return worst
