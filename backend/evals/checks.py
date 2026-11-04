# author: szw
"""评测判分规则。

刻意不复用 backend/app 下的任何实现。用被测代码判断被测对象，等于自己给自己
批卷：哪天把质检规则改坏了，评测会跟着一起错，结果仍然全绿。这里把每一条
硬约束独立重写一遍，改坏了才会红。

只判"结构"，不判"内容"。硬断言"第 1 天必须去星海广场"这类写法一定会 flaky——
模型每次给的景点不同，最后只能不断改测试去迁就模型，测试也就失去意义了。
产品的承诺是"三天就是三天、每天有具体去处、不超预算、地图能画线"，
断言就落在这几句上。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------- 词典

# 出现这些词，说明这条活动写的是一个"具体地方"，而不是"自由活动"这种空话
_PLACE_SUFFIX = (
    "公园", "广场", "博物馆", "美术馆", "纪念馆", "展览馆", "科技馆", "图书馆",
    "海滩", "沙滩", "海水浴场", "浴场", "海岛", "岛", "湖", "湿地", "温泉",
    "山", "峰", "岭", "谷", "溪", "泉", "瀑布", "森林", "草原",
    "寺", "庙", "宫", "塔", "教堂", "遗址", "古城", "古镇", "城墙", "关",
    "楼", "阁", "亭", "祠", "牌坊",
    "步行街", "街", "巷", "桥", "码头", "港", "湾", "灯塔",
    "景区", "度假区", "乐园", "动物园", "植物园", "园", "馆",
    "风貌区", "风情区", "文化街区", "商圈", "老街", "古街", "坊",
    "站", "机场", "剧院", "影院", "故居", "书院", "大学",
)

# 这些开头的活动没有"去处"，统计具体地名时整条排除。
# 刻意不收"美食""小吃"：它们常是地名的一部分（美食街、小吃城），
# 一刀切排除会把"逛回民街美食街"这种正经安排判成空话。
_NON_PLACE = (
    "早餐", "午餐", "晚餐", "夜宵", "用餐", "吃饭", "餐：",
    "入住", "退房", "酒店", "民宿", "住宿", "休息", "整顿", "整理行李",
    "自由活动", "自由探索",
)

# 交通类动词：只剥掉动词，后面跟的地名仍然算数。
# 一刀切排除会把"前往金石滩海滨浴场"这种最典型的景点安排误杀成空话。
_LOGISTICS_PREFIX = (
    "前往", "抵达", "出发", "乘车", "返回", "返程", "赶赴", "步行至", "驱车",
)

# 内部标记：这些词只应存在于 prompt / 日志里，绝不该出现在给用户的产出中
_LEAK_MARKERS = (
    "REVIEW_RULE", "系统提示", "system prompt", "TripState", "draft_plan",
    "llm_extract", "mock", "以下是你的角色设定",
)


# ---------------------------------------------------------------- 工具


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _as_number(value: Any) -> Optional[float]:
    """行程里的 cost 可能是 180 / "180" / "180元" / ""，统一转成数字。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def _day_activities(day: Dict[str, Any]) -> List[Dict[str, Any]]:
    acts = day.get("activities")
    return acts if isinstance(acts, list) else []


def _act_text(act: Dict[str, Any]) -> str:
    return f"{_as_text(act.get('activity'))} {_as_text(act.get('description'))}"


def _is_named_place(act: Dict[str, Any]) -> bool:
    """这条活动写的是不是"某个具体地方"。

    先排除餐饮住宿（"午餐：海鲜大排档"里有"排"，但不该算景点），
    再剥掉"前往/抵达/返回"这类动词，剩下部分带地名后缀才算数。
    """
    head = _as_text(act.get("activity"))
    if len(head) < 3:
        return False
    for word in _NON_PLACE:
        if head.startswith(word):
            return False

    body = head
    for verb in _LOGISTICS_PREFIX:
        if body.startswith(verb):
            body = body[len(verb):].lstrip("：:，, ")
            break
    if len(body) < 2:
        return False
    return any(word in body for word in _PLACE_SUFFIX)


def _plan_text(plan: Iterable[Dict[str, Any]]) -> str:
    """把整份行程拼成一段文本，用于"景点名是否真的被提到过"的核对。"""
    parts: List[str] = []
    for day in plan:
        for act in _day_activities(day):
            parts.append(_act_text(act))
        for key in ("theme", "hotel", "transport"):
            parts.append(_as_text(day.get(key)))
        meals = day.get("meals")
        if isinstance(meals, list):
            parts.extend(_as_text(m) for m in meals)
    return " ".join(parts)


def _name_variants(name: str) -> List[str]:
    """中文景点名的常见变形。

    三类：去括号（"西安城墙（永宁门）"→"西安城墙"）、截短
    （"老虎滩海洋公园"→"老虎滩"）、去城市前缀（原文写"钟楼与鼓楼"，
    提取器给出"西安鼓楼"）。后两种都是提取器正常行为，不做变换就会
    把正常提取误判成幻觉。
    """
    base = _as_text(name)
    if not base:
        return []

    plain = re.sub(r"[（(][^)）]*[)）]", "", base).strip()
    seeds = [base] if plain == base or not plain else [base, plain]

    variants: List[str] = []
    for seed in seeds:
        variants.append(seed)
        for suffix in (
            "国家旅游度假区", "旅游度假区", "海洋公园", "森林公园", "地质公园",
            "公园", "广场", "景区", "度假区", "步行街", "博物馆", "机场", "站",
            "大学", "寺", "岛", "山", "园", "馆",
        ):
            if seed.endswith(suffix) and len(seed) > len(suffix) + 1:
                variants.append(seed[: -len(suffix)])
        # 去掉 2~3 字的城市前缀（西安鼓楼 → 鼓楼），剩余部分仍要有 2 字以上
        for cut in (2, 3):
            if len(seed) - cut >= 2:
                variants.append(seed[cut:])
    return variants


def name_mentioned(name: str, text: str) -> bool:
    """景点名（或它的截短形式）是否在行程原文里出现过。"""
    return any(v and v in text for v in _name_variants(name))


# ---------------------------------------------------------------- 主入口


def check_case(case: Dict[str, Any], result: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """返回 (failures, warnings)。

    result 由 runner 组装：
        intent     plan / clarify / chat / cancelled / failed
        state      最终 state（dict）
        interrupts 触发 HITL 的次数
        error      运行期异常（超时、崩溃）
    """
    expect = case.get("expect") or {}
    failures: List[str] = []
    warnings: List[str] = []

    if result.get("error"):
        return [f"[运行] {result['error']}"], warnings

    state: Dict[str, Any] = result.get("state") or {}
    plan: List[Dict[str, Any]] = state.get("draft_plan") or []
    intent = result.get("intent")

    # ---- 分支：该走规划却去追问/闲聊，是本轮最严重的失败
    want_intent = expect.get("intent", "plan")
    if want_intent != "any" and intent != want_intent:
        failures.append(f"[分支] 期望 {want_intent}，实际 {intent}")

    # ---- 运行期异常：节点自己报了错
    for key in ("error", "guides_error", "attractions_error", "weather_error"):
        if _as_text(state.get(key)):
            failures.append(f"[运行] {key}: {_as_text(state.get(key))}")

    # clarify 类用例只看分支，不看行程
    if want_intent == "clarify":
        question = _as_text(state.get("clarify_question") or state.get("response"))
        if not question:
            failures.append("[追问] clarify 分支没有给出追问话术")
        if len(question) > 200:
            warnings.append(f"[追问] 追问话术偏长（{len(question)} 字）")
        return failures, warnings

    if intent != "plan":
        return failures, warnings

    # ---- 天数：解析出来的约束要对，产出的天数也要对
    want_days = expect.get("days")
    if want_days:
        parsed_days = _as_number(state.get("days"))
        if parsed_days != want_days:
            failures.append(f"[解析] 天数解析成 {parsed_days}，用户说的是 {want_days}")
        if len(plan) != want_days:
            failures.append(f"[天数] 产出 {len(plan)} 天，期望 {want_days} 天")

    if not plan:
        failures.append("[天数] 没有产出任何行程")
        return failures, warnings

    # ---- 每天的活动数：2~6 是硬约束（与产品规则一致），3 条以下只提醒
    min_acts = int(expect.get("min_acts", 2))
    max_acts = int(expect.get("max_acts", 6))
    for idx, day in enumerate(plan, 1):
        day_no = day.get("day") or idx
        count = len(_day_activities(day))
        if count < min_acts or count > max_acts:
            failures.append(f"[活动数] 第 {day_no} 天 {count} 个活动，应在 {min_acts}~{max_acts}")
        elif count < 3:
            warnings.append(f"[活动数] 第 {day_no} 天只有 {count} 个活动")

    # ---- 具体地名：防止"上午自由活动、下午休息"这种空心行程
    min_named = int(expect.get("min_named_per_day", 0))
    if min_named:
        for idx, day in enumerate(plan, 1):
            day_no = day.get("day") or idx
            named = [a for a in _day_activities(day) if _is_named_place(a)]
            if len(named) < min_named:
                names = "；".join(_as_text(a.get("activity"))[:16] for a in _day_activities(day))
                failures.append(
                    f"[具体地名] 第 {day_no} 天只有 {len(named)} 处具体地点，"
                    f"要求 >= {min_named}（{names}）"
                )

    # ---- 预算：只算活动费用（与规划师的 cost 字段对齐）
    budget = _as_number(state.get("budget"))
    ratio_max = expect.get("budget_ratio_max")
    if budget and ratio_max:
        total = sum(_as_number(a.get("cost")) or 0 for d in plan for a in _day_activities(d))
        if total > budget * float(ratio_max):
            failures.append(
                f"[预算] 活动费合计 {total:.0f} 元，超过预算 {budget:.0f} 元"
                f"（上限倍数 {ratio_max}）"
            )

    # ---- 地图
    map_data = state.get("map_data") or {}
    if expect.get("map_required", True):
        attractions = map_data.get("attractions") or []
        routes = map_data.get("routes") or []
        if not attractions:
            failures.append("[地图] 没有生成任何景点坐标")
        if plan and not routes:
            warnings.append("[地图] 没有生成任何路线（可能是每天景点太少）")

        text = _plan_text(plan)
        phantom = [a.get("name") for a in attractions if not name_mentioned(a.get("name"), text)]
        if phantom:
            failures.append(f"[幻觉] 地图出现行程原文没提过的地点：{phantom}")

        no_day = [r for r in routes if r.get("day") in (None, "", 0)]
        if no_day:
            failures.append(f"[路线] {len(no_day)} 段路线缺少 day 字段，前端无法按天分组")

        if routes:
            points = [len(r.get("polyline") or []) for r in routes]
            if max(points) <= 2:
                failures.append(f"[折线] 所有路段折线都不超过 2 个点，地图上只会是直线（{points}）")
            elif sum(1 for p in points if p <= 2) == len(points):
                warnings.append("[折线] 每段都只有端点，可能退回了兜底逻辑")
            approx = [a.get("name") for a in attractions if a.get("approx")]
            if approx:
                warnings.append(f"[地图] {len(approx)} 个景点未精确定位：{approx}")

            # 虚构地点测不出真假，但"定位不到"是可测的：编出来的景点高德查无此地，
            # 会全部落到 approx 估算坐标上。这条只对幻觉诱饵用例生效，
            # 偏远地区本身就难定位，所以不做全局默认。
            ratio_cap = expect.get("max_approx_ratio")
            if ratio_cap is not None and attractions:
                ratio = len(approx) / len(attractions)
                if ratio > float(ratio_cap):
                    failures.append(
                        f"[幻觉] {len(approx)}/{len(attractions)} 个景点定位不到"
                        f"（{ratio:.0%} > {float(ratio_cap):.0%}），疑似编造地名：{approx}"
                    )

    # ---- 幻觉诱饵：虚构景点名不该原样出现在产出里
    for fake in expect.get("forbid_names") or []:
        if fake and fake in _plan_text(plan):
            failures.append(f"[幻觉] 行程里出现了应被识别的虚构地点：{fake}")

    # ---- 多城市混搭：目的地只能是一个城市
    max_cities = expect.get("max_cities")
    if max_cities:
        dest = _as_text(state.get("destination"))
        city_count = dest.count("市") or (1 if dest else 0)
        if city_count > int(max_cities):
            failures.append(f"[城市] 目的地解析出多个城市：{dest}")

    # ---- 提示注入：内部标记不该漏进产出
    dump = _plan_text(plan) + " " + _as_text(state.get("response"))
    for marker in _LEAK_MARKERS:
        if marker and marker in dump:
            failures.append(f"[注入] 产出里出现了内部标记：{marker}")

    # ---- HITL：该确认的没确认，说明中断点被绕过了
    if expect.get("confirm") is True and not result.get("interrupts"):
        failures.append("[确认] 没有触发人工确认中断")

    # ---- 质检：不该每轮都被打回
    if state.get("review_passed") is False and (state.get("review_round") or 0) >= 2:
        issues = state.get("review_issues") or []
        warnings.append(f"[质检] 重排次数用尽仍未通过：{issues[:2]}")

    # ---- 约束自相矛盾：可以放行，但必须让用户看见提示
    conflicts = state.get("review_conflicts") or []
    if conflicts:
        tips = [str(t) for t in (state.get("plan_tips") or [])]
        if not any("冲突" in t for t in tips):
            failures.append("[冲突] 识别出约束矛盾却只字未提，用户会以为系统忽略了他的要求")
        else:
            warnings.append(f"[冲突] 已按用户要求交付并提示：{conflicts[:1]}")

    return failures, warnings
