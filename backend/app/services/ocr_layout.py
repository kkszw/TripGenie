# author: szw
"""OCR 阅读顺序还原：把检测框排成人类阅读顺序。

为什么需要这一步：RapidOCR 返回的框是按**全局 y 从上到下**排的。遇到多栏排版
（杂志式攻略、带侧栏的行程单），左右两栏会被逐行交错拼起来——

    左栏第 1 行 + 右栏第 1 行 + 左栏第 2 行 + 右栏第 2 行 ...

切块之后每一块都是半句接半句，向量和 BM25 都会被污染。实测一份沈阳攻略
（双栏，左栏 x≈130-720、右栏 x≈766-1360）就是这个现象。

算法用递归 XY-cut：在区域里找「没有框跨过的横向/纵向空白带」，按空白带切成
子区域，再递归；切不动时按行归并。优先竖切（分栏），因为中文正文的栏间距
总是贯穿整个区域高度，而栏内的行间距不会横跨两栏。
"""
from typing import Dict, List, Optional, Sequence

# 空白带最小宽度，占区域宽/高的比例。低于这个比例的缝隙多半是字间距，不能切
MIN_GAP_RATIO = 0.012
# 区域太小或递归太深就停，避免把一行字切成碎片
MAX_DEPTH = 8
MIN_ITEMS_TO_CUT = 2
# 同一行的判定：纵向重叠超过较矮那个框高度的这个比例
SAME_LINE_OVERLAP = 0.5


def _to_boxes(items: Sequence[Dict]) -> List[Dict]:
    return [{
        "text": str(item.get("text") or ""),
        "x0": float(item["x0"]), "y0": float(item["y0"]),
        "x1": float(item["x1"]), "y1": float(item["y1"]),
    } for item in items if str(item.get("text") or "").strip()]


def _best_gap(intervals: List[tuple], low: float, high: float,
              min_gap: float) -> Optional[tuple]:
    """在一维区间集合里找最宽的空白带，返回 (起点, 终点)，找不到返回 None。"""
    if len(intervals) < 2:
        return None
    ordered = sorted(intervals)
    cursor = low
    best = None
    for start, end in ordered:
        if start - cursor >= min_gap and (best is None or start - cursor > best[1] - best[0]):
            best = (cursor, start)
        cursor = max(cursor, end)
    return best


def _cut_axis(items: List[Dict], axis: str, low: float, high: float) -> Optional[List[List[Dict]]]:
    """沿 axis 切一刀，返回两组框；切不动返回 None。"""
    span = high - low
    if span <= 0:
        return None
    start_key, end_key = ("x0", "x1") if axis == "x" else ("y0", "y1")
    intervals = [(item[start_key], item[end_key]) for item in items]
    gap = _best_gap(intervals, low, high, span * MIN_GAP_RATIO)
    if not gap:
        return None
    middle = (gap[0] + gap[1]) / 2
    near = [item for item in items if (item[start_key] + item[end_key]) / 2 < middle]
    far = [item for item in items if (item[start_key] + item[end_key]) / 2 >= middle]
    if not near or not far:  # 缝隙落在空白处，两边没分开
        return None
    return [near, far]


def _group_lines(items: List[Dict]) -> List[str]:
    """叶子区域：按 y 归并成行，行内按 x 拼接。"""
    if not items:
        return []
    ordered = sorted(items, key=lambda item: (item["y0"], item["x0"]))
    lines: List[List[Dict]] = []
    for item in ordered:
        placed = False
        for line in lines:
            top = max(line[0]["y0"], item["y0"])
            bottom = min(line[0]["y1"], item["y1"])
            shorter = min(line[0]["y1"] - line[0]["y0"], item["y1"] - item["y0"]) or 1
            if (bottom - top) / shorter >= SAME_LINE_OVERLAP:
                line.append(item)
                placed = True
                break
        if not placed:
            lines.append([item])
    lines.sort(key=lambda line: min(part["y0"] for part in line))
    texts: List[str] = []
    for line in lines:
        parts = sorted(line, key=lambda part: part["x0"])
        joined = " ".join(part["text"].strip() for part in parts if part["text"].strip())
        if joined:
            texts.append(joined)
    return texts


def _cut(items: List[Dict], bounds: Dict, depth: int) -> List[str]:
    if len(items) < MIN_ITEMS_TO_CUT or depth >= MAX_DEPTH:
        return _group_lines(items)

    groups = _cut_axis(items, "x", bounds["x0"], bounds["x1"])
    if groups:  # 优先竖切：分栏是最常见的乱序来源
        left, right = groups
        return _cut(left, _sub_bounds(bounds, left), depth + 1) + \
            _cut(right, _sub_bounds(bounds, right), depth + 1)

    groups = _cut_axis(items, "y", bounds["y0"], bounds["y1"])
    if groups:
        top, bottom = groups
        return _cut(top, _sub_bounds(bounds, top), depth + 1) + \
            _cut(bottom, _sub_bounds(bounds, bottom), depth + 1)

    return _group_lines(items)


def _sub_bounds(bounds: Dict, items: List[Dict]) -> Dict:
    """子区域的边界收窄到这组框的实际范围，避免用全页宽高算比例导致切不开。"""
    return {
        "x0": min(item["x0"] for item in items),
        "y0": min(item["y0"] for item in items),
        "x1": max(item["x1"] for item in items),
        "y1": max(item["y1"] for item in items),
    }


def order_items(items: Sequence[Dict], width: float = 0, height: float = 0) -> List[str]:
    """检测框 → 阅读顺序的文本行。

    Args:
        items: [{"text": str, "x0","y0","x1","y1": float}]
        width/height: 页面尺寸，仅用于确定初始边界；传 0 则按框的包围盒推断。

    Returns:
        文本行列表，按阅读顺序排列。
    """
    boxes = _to_boxes(items)
    if not boxes:
        return []
    bounds = {
        "x0": min(item["x0"] for item in boxes),
        "y0": min(item["y0"] for item in boxes),
        "x1": max(max(item["x1"] for item in boxes), width or 0),
        "y1": max(max(item["y1"] for item in boxes), height or 0),
    }
    return _cut(boxes, bounds, 0)


def boxes_from_ocr_result(result, width: float = 0, height: float = 0) -> List[str]:
    """把 RapidOCR 的结果对象转成阅读顺序的文本行。"""
    texts = list(getattr(result, "txts", None) or [])
    boxes = getattr(result, "boxes", None)
    if not texts:
        return []
    if boxes is None or len(boxes) != len(texts):
        # 拿不到几何信息时，退回检测顺序（总比丢掉内容好）
        return [str(text).strip() for text in texts if str(text).strip()]

    items: List[Dict] = []
    for text, box in zip(texts, boxes):
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        items.append({"text": str(text), "x0": min(xs), "x1": max(xs),
                      "y0": min(ys), "y1": max(ys)})
    return order_items(items, width=width, height=height)
