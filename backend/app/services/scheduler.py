# author: szw
"""行程排程：把"几点到几点"从 LLM 手里拿回来。

基线实测（15 条历史行程，见 scripts/schedule_baseline.py）：**33.7% 的相邻活动
间隔不足 15 分钟，33.2% 干脆首尾相接**——上一个 10:30 结束，下一个 10:30 开始。
LLM 排时间时不做算术，它给的是"看起来合理"的数字；而 code_review 的五条规则
只抓天数/预算/活动数/时间顺序/景点重复，15 条里只报了 1 条违规，抓不到这类问题。

所以这里做三件纯确定性的事：
  1. **天内重排**：同一天的景点按地理邻近排序（最近邻 + 2-opt），避免东一头西一头；
  2. **时间轴重算**：从 09:00 起逐段加上通勤时间，午餐/晚餐仍固定在它原本的时段；
  3. **超时移出**：排不进闭店时间（默认 20:00）的活动从当天摘掉，交给调用方
     明确告诉用户"这个点没排进去"，而不是硬塞一个物理上到不了的时间。

边界很明确：**去哪几个景点仍然由 LLM 决定**（那是语义选择），这里只决定顺序和
钟点。跨天重新分配（比如崂山和栈桥被排进同一天）不在本模块范围内——那要改
LLM 的输入，不是改它的输出。

通勤时长用球面距离估算（见 estimate_travel_minutes）：排序不需要精确到分钟，
需要的是"哪两个点在同一片"。生成地图时高德会给真实 duration，那时才是准确值。
"""
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

Coord = Tuple[float, float]

# 一天的可用区间。09:00 是"吃完早饭出门"的默认，20:00 是大多数景点/商圈的收摊线。
DAY_START = 9 * 60
DAY_END = 20 * 60

# 相邻活动间隔低于这个值就算"没留通勤时间"。市内打车/步行都在 5 分钟以上，
# 再叠加找路、停车、检票，15 分钟是个保守阈值。
MIN_TRANSFER_MIN = 15

# 通勤估算里的固定开销：出停车场、找入口、检票。没有它，700 米的两个景点
# 只会被算成 3 分钟，排出来的间隔依然是"物理上做不到"。
TRAVEL_OVERHEAD_MIN = 10.0

# 上一个活动位置未知时（比如刚吃完饭、刚办完入住），下一段至少留这么多分钟。
# 与 MIN_TRANSFER_MIN 齐平：宁可保守，也不要排出"办完入住立刻出现在景区"。
UNKNOWN_ORIGIN_MIN = 15.0

# 同一个地点内的两个活动（"前往金石滩" → "金石滩漫步"）间隔可以短于阈值，
# 因为中间没有真正的位移。用这个半径判定"同一个地方"。
SAME_PLACE_KM = 0.5

# 进入餐厅/回酒店前也要走一段路，否则"景点 12:58 结束、午饭 12:58 开始"仍是零间隔。
# 取 15 分钟与 MIN_TRANSFER_MIN 齐平：从景区出来走到餐厅、等位，就是这么久。
MEAL_GAP_MIN = 15.0

# 单个活动时长的合理区间：低于 15 分钟的"游览"没有意义，超过 5 小时的
# 多半是 LLM 把"一整天"写成了时间跨度，压缩到 5 小时更贴近真实节奏。
MIN_ACTIVITY_MIN = 15
MAX_ACTIVITY_MIN = 300

_LOGISTICS_HINTS = ("入住", "抵达", "返程", "离开", "退房", "车站", "机场", "高铁", "取车")
_MEAL_HINTS = ("早餐", "午餐", "午餐：", "晚餐", "晚餐：", "夜宵", "用餐")

# 长词必须排在短词前面，否则"乘车前往"会被"乘"先吃掉，剩下"车前往泰山"。
_LEAD_VERBS = (
    "乘车前往", "步行前往", "乘坐", "游览", "参观", "前往", "漫步", "打卡",
    "体验", "欣赏", "逛", "乘", "去", "到",
)
_TAIL_NOISE = ("自由活动", "看日落", "看日出", "散步", "漫步", "游玩", "观光",
               "骑行", "游览", "拍照", "打卡", "闲逛")
_PUNCT = "，,、。；;（(：:"


def _minutes(value) -> Optional[int]:
    """'09:30' -> 570。解析不出来返回 None。"""
    match = re.match(r"\s*(\d{1,2})\s*[:：]\s*(\d{2})", str(value or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if 0 <= hour < 24 and 0 <= minute < 60:
        return hour * 60 + minute
    return None


def parse_span(value) -> Optional[Tuple[int, int]]:
    """'09:00-10:30' -> (540, 630)。跨零点或格式不对返回 None。"""
    start = _minutes(value)
    if start is None:
        return None
    parts = re.split(r"[-~—至]", str(value))
    if len(parts) < 2:
        return None
    end = _minutes(parts[-1])
    if end is None or end <= start:
        return None
    return start, end


def clock(minutes) -> str:
    minutes = max(0, int(round(minutes)))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def meal_floor(text, day_start: int) -> int:
    """吃饭类活动的时间下限：早饭跟着出门，午饭不早于 11:30，晚饭不早于 17:30。

    LLM 常把午饭排到 11:00（因为前面的顺序乱了），硬保留这个点会把上午压成空白。
    这里只当下限用：前面的景点排到几点，饭就从几点开始。
    """
    content = str(text or "")
    if "早餐" in content or "早饭" in content:
        return day_start
    if "午餐" in content or "午饭" in content:
        return max(day_start, 11 * 60 + 30)
    if "晚餐" in content or "晚饭" in content:
        return max(day_start, 17 * 60 + 30)
    return day_start


def meal_ceiling(text, day_end: int) -> int:
    """吃饭类活动最晚几点开始。

    它同时是**前面景点**的截止线：晚饭最晚 19:00 开始，那么排在晚饭前的景点
    必须在那之前结束，排不下就该摘掉——否则晚饭会被挤到 19:40，然后和
    上一个活动的时间重叠（这是最初版本真实踩到的 bug）。
    """
    content = str(text or "")
    if "早餐" in content or "早饭" in content:
        return min(day_end, 11 * 60)
    if "午餐" in content or "午饭" in content:
        return min(day_end, 13 * 60 + 30)
    if "晚餐" in content or "晚饭" in content:
        return min(day_end, 19 * 60)
    return day_end


def earliest_minute(text, day_start: int) -> int:
    """活动的起步下限。

    排程只看坐标，不看语义，但有些活动天然绑定时段：夜景/夜市必须在晚上，
    日出必须在清晨。不设这个下限，LLM 写在最后的"滨海路夜景散步"会被排到
    下午三点——时间轴自洽，内容却不对。
    """
    content = str(text or "")
    if any(hint in content for hint in ("夜景", "夜游", "夜市", "酒吧", "宵夜")):
        return max(day_start, 19 * 60)
    if "日出" in content:
        return day_start
    return max(day_start, meal_floor(text, day_start))


def classify(text) -> str:
    """把一条活动分成 spot / meal / logistics。

    只用于排程，不参与内容判定，所以规则宁宽勿严：认不出来就当景点，
    最多是多排一次顺序，不会丢内容。
    """
    content = str(text or "").strip()
    if any(hint in content for hint in _LOGISTICS_HINTS):
        return "logistics"
    if (any(content.startswith(hint) for hint in _MEAL_HINTS)
            or any(f"{hint}：" in content[:8] for hint in _MEAL_HINTS)):
        return "meal"
    return "spot"


def place_name(text) -> Optional[str]:
    """从活动描述里抽景点名，供地理编码用。

    '游览中山广场，欣赏俄式建筑群' -> '中山广场'
    '星海广场散步，欣赏跨海大桥'   -> '星海广场'
    抽不出来就返回 None，这条活动会保持原位、不参与重排。
    """
    content = str(text or "").strip()
    for verb in _LEAD_VERBS:
        if content.startswith(verb):
            content = content[len(verb):]
            break
    cut = len(content)
    for mark in _PUNCT:
        index = content.find(mark)
        if 0 < index < cut:
            cut = index
    content = content[:cut].strip()
    for tail in _TAIL_NOISE:
        if content.endswith(tail) and len(content) > len(tail):
            content = content[: -len(tail)]
            break
    content = content.strip()
    if not 2 <= len(content) <= 20:
        return None
    return content


def haversine_km(a: Coord, b: Coord) -> float:
    lng1, lat1 = float(a[0]), float(a[1])
    lng2, lat2 = float(b[0]), float(b[1])
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    h = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, h)))


def estimate_travel_minutes(a: Optional[Coord], b: Optional[Coord]) -> float:
    """两点之间的驾车耗时估算（分钟）。

    `1.35` 是道路曲折系数（直线距离乘以它才接近实际里程）；速度分三档：
    短途受红绿灯和找车位拖累，长途才跑得起来。另加 8 分钟固定开销（出停车场、
    找入口、检票），否则 700 米的两个景点会被算成 3 分钟——实测步行都不够。

    **这是估算**，用于排序和留白；真实耗时以高德返回的 duration 为准。
    """
    if not a or not b:
        return 0.0
    km = haversine_km(a, b) * 1.35
    if km <= 3:
        speed = 18.0
    elif km <= 15:
        speed = 28.0
    else:
        speed = 50.0
    return TRAVEL_OVERHEAD_MIN + km / speed * 60.0


def _travel_matrix(coords: List[Coord]) -> List[List[float]]:
    size = len(coords)
    matrix = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1, size):
            distance = haversine_km(coords[i], coords[j])
            matrix[i][j] = matrix[j][i] = distance
    return matrix


def order_points(coords: List[Coord], start: Optional[Coord] = None) -> List[int]:
    """最近邻 + 2-opt，返回点的访问顺序（下标）。点很少（一天 3~6 个），够用。"""
    size = len(coords)
    if size <= 2:
        return list(range(size))

    matrix = _travel_matrix(coords)
    start_index = 0
    if start:
        start_index = min(range(size), key=lambda i: haversine_km(start, coords[i]))

    order = [start_index]
    remaining = set(range(size)) - {start_index}
    while remaining:
        last = order[-1]
        nxt = min(remaining, key=lambda i: matrix[last][i])
        order.append(nxt)
        remaining.discard(nxt)

    # 2-opt：反复尝试反转区间，能缩短总里程就接受。上限 50 轮防死循环。
    improved = True
    rounds = 0
    while improved and rounds < 50:
        improved = False
        rounds += 1
        for i in range(1, size - 1):
            for j in range(i + 1, size):
                before = matrix[order[i - 1]][order[i]] + (
                    matrix[order[j]][order[j + 1]] if j + 1 < size else 0.0)
                after = matrix[order[i - 1]][order[j]] + (
                    matrix[order[i]][order[j + 1]] if j + 1 < size else 0.0)
                if after + 1e-9 < before:
                    order[i:j + 1] = reversed(order[i:j + 1])
                    improved = True
    return order


@dataclass
class ScheduleResult:
    """一次排程的结果。days 与输入同构（只改了 time），dropped 是排不进去的活动。"""

    days: List[dict] = field(default_factory=list)
    dropped: List[dict] = field(default_factory=list)
    stats: Dict = field(default_factory=dict)


def schedule_day(activities: List[dict], coord_of: Callable[[str], Optional[Coord]],
                 day_start: int = DAY_START, day_end: int = DAY_END) -> Tuple[List[dict], List[dict]]:
    """重排一天的活动，返回 (已排好的活动, 排不下的活动)。

    coord_of: 景点名 -> (lng, lat)；返回 None 表示没有坐标，这条活动保持原位。
    """
    items = []
    for index, activity in enumerate(activities or []):
        span = parse_span(activity.get("time"))
        duration = (span[1] - span[0]) if span else 60
        duration = max(MIN_ACTIVITY_MIN, min(duration, MAX_ACTIVITY_MIN))
        kind = classify(activity.get("activity"))
        name = place_name(activity.get("activity")) if kind == "spot" else None
        items.append({
            "act": dict(activity),
            "kind": kind,
            "dur": duration,
            "name": name,
            "coord": coord_of(name) if name else None,
            "seq": index,
            "floor": earliest_minute(activity.get("activity"), day_start),
        })

    movable = [it for it in items if it["kind"] == "spot" and it["coord"]]
    anchors = [it for it in items if not (it["kind"] == "spot" and it["coord"])]

    # 一个坐标都没拿到（地理编码全失败、或整天的活动都抽不出地名）：
    # 这时候重排没有依据，动时间只会把本来合理的行程搞乱，直接原样返回。
    if not movable:
        return [dict(activity) for activity in activities or []], []

    original_spots = list(movable)
    if movable:
        anchor_coord = next((it["coord"] for it in anchors if it["coord"]), None)
        order = order_points([it["coord"] for it in movable], anchor_coord)
        movable = [movable[i] for i in order]

    # 锚（午餐/晚餐/抵达/入住）保留它在原序列里的相对位置：它前面原来有几个景点，
    # 重排后就还排在那几个景点之后。前面一个景点都没有的（抵达、入住）放在最前；
    # 它的钟点只作为下限（午饭不早于 11:30），不做硬截止——否则一个 11:00 的午餐
    # 会把整个上午压成空白。
    prefix_anchors: List[dict] = []
    anchor_by_index: Dict[int, List[dict]] = {}
    tail_anchors: List[dict] = []
    for anchor in anchors:
        ahead = sum(1 for it in original_spots if it["seq"] < anchor["seq"])
        if ahead == 0:
            prefix_anchors.append(anchor)
        elif ahead >= len(movable):
            tail_anchors.append(anchor)
        else:
            anchor_by_index.setdefault(ahead - 1, []).append(anchor)

    merged: List[dict] = list(prefix_anchors)
    for index, spot in enumerate(movable):
        merged.append(spot)
        merged.extend(anchor_by_index.get(index, []))
    merged.extend(sorted(tail_anchors, key=lambda it: it["seq"]))

    scheduled: List[dict] = []
    dropped: List[dict] = []
    cursor = day_start
    prev_coord: Optional[Coord] = None

    def travel_to(coord: Optional[Coord]) -> float:
        if not coord:
            return 0.0
        if prev_coord is None:
            # 起点未知（刚吃完饭/刚办完入住）：不能按零通勤算，给一个下限。
            return UNKNOWN_ORIGIN_MIN
        return estimate_travel_minutes(prev_coord, coord)

    # 每项活动的截止时间：等于它后面最近那个"饭点"的上限。
    # 反向扫一遍即可，因为限制只会向后累积、不会向前传播。
    limits = [day_end] * len(merged)
    ceiling = day_end
    for index in range(len(merged) - 1, -1, -1):
        item = merged[index]
        if item["kind"] != "spot" or not item["coord"]:
            ceiling = min(ceiling, meal_ceiling(item["act"].get("activity"), day_end))
        limits[index] = ceiling

    for index, item in enumerate(merged):
        if item["kind"] == "spot" and item["coord"]:
            start = max(cursor + travel_to(item["coord"]), item["floor"])
            end = start + item["dur"]
            if end > limits[index]:
                # 排在这之前的饭点要求它必须更早结束，说明当天真的塞不下。
                dropped.append(item["act"])
                continue
        else:
            # 吃饭/入住不丢内容：先保住下限，实在挤不下才压缩剩余时长。
            start = max(cursor + MEAL_GAP_MIN, item["floor"])
            end = start + item["dur"]
            if end > day_end:
                end = day_end
                start = min(start, end - MIN_ACTIVITY_MIN)

        item["act"]["time"] = f"{clock(start)}-{clock(end)}"
        scheduled.append(item)
        cursor = end
        if item["coord"]:
            prev_coord = item["coord"]

    scheduled.sort(key=lambda it: (parse_span(it["act"].get("time")) or (day_end,))[0])
    return [it["act"] for it in scheduled], dropped


def schedule_stats(days: List[dict], coord_of: Callable[[str], Optional[Coord]]) -> Dict:
    """排程质量：短间隔占比与估算通勤总时长。用于前后对比，不参与业务流程。

    同一个地点内的连续活动（"前往金石滩" → "金石滩漫步"）不计入短间隔：
    它们之间没有真实位移，用 15 分钟去要求反而不合理。
    """
    pairs = 0
    short = 0
    travel = 0.0
    for day in days or []:
        activities = day.get("activities") or []
        previous_end: Optional[int] = None
        previous_coord: Optional[Coord] = None

        for activity in activities:
            span = parse_span(activity.get("time"))
            name = place_name(activity.get("activity"))
            coord = coord_of(name) if name else None

            if span and previous_end is not None:
                same_place = (
                    previous_coord is not None and coord is not None
                    and haversine_km(previous_coord, coord) <= SAME_PLACE_KM
                )
                pairs += 1
                if not same_place and span[0] - previous_end < MIN_TRANSFER_MIN:
                    short += 1

            if span:
                previous_end = span[1]
            if coord:
                if previous_coord is not None and classify(activity.get("activity")) == "spot":
                    travel += estimate_travel_minutes(previous_coord, coord)
                previous_coord = coord

    return {
        "time_pairs": pairs,
        "short_gap_pairs": short,
        "short_gap_ratio": round(short / pairs, 3) if pairs else None,
        "travel_minutes": round(travel, 1),
    }


def _day_end_minute(day: dict) -> int:
    spans = [parse_span(a.get("time")) for a in day.get("activities") or []]
    spans = [span for span in spans if span]
    return max((span[1] for span in spans), default=DAY_START)


def _rebalance(new_days: List[dict], dropped: List[dict],
               coord_of: Callable[[str], Optional[Coord]],
               day_start: int, day_end: int) -> List[dict]:
    """把当天排不下的活动挪到还有余量的另一天。

    只做一轮、且只在"挪过去之后那天也不丢活动"时才接受，避免拆东墙补西墙。
    现实场景：县城周边景点少但彼此几十公里（临潭的冶海、尕海湖），LLM 会把
    它们塞进同一天，硬排必然丢掉一两个；而另一天往往空着。丢弃是最差的选择。
    """
    remaining: List[dict] = []
    for item in dropped:
        source_day = item.get("day")
        # "day" 是报告用的标记，不属于活动本身，挪去别天时要摘掉
        payload = {key: value for key, value in item.items() if key != "day"}
        candidates = sorted(
            (day for day in new_days if day.get("day") != source_day),
            key=_day_end_minute,
        )
        placed = False
        for target in candidates:
            # 余量不足一小时就别塞了：挤进去只会把别的活动挤出去。
            if _day_end_minute(target) > day_end - 60:
                continue
            activities = list(target.get("activities") or []) + [dict(payload)]
            scheduled, lost = schedule_day(activities, coord_of, day_start, day_end)
            if lost:
                continue
            target["activities"] = scheduled
            placed = True
            break
        if not placed:
            remaining.append(item)
    return remaining


def optimize_days(days: List[dict], coord_of: Callable[[str], Optional[Coord]],
                  day_start: int = DAY_START, day_end: int = DAY_END) -> ScheduleResult:
    """逐天重排，再把排不下的活动尝试挪到别天。输入输出都是 days[] 结构。"""
    new_days: List[dict] = []
    dropped: List[dict] = []

    for day in days or []:
        scheduled, lost = schedule_day(day.get("activities") or [], coord_of, day_start, day_end)
        day_no = day.get("day")
        dropped.extend({**activity, "day": day_no} for activity in lost)
        new_day = dict(day)
        new_day["activities"] = scheduled
        new_days.append(new_day)

    dropped = _rebalance(new_days, dropped, coord_of, day_start, day_end)

    return ScheduleResult(
        days=new_days,
        dropped=dropped,
        stats=schedule_stats(new_days, coord_of),
    )
