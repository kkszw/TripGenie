# author: szw
"""行程质检节点（Evaluator）。

两层校验：
  第一层 _code_review：天数、预算、时段、重复景点——纯算术，零成本、零误判；
  第二层 LLM：只剩"城市错位"和"偏好冲突"需要常识，输出必须带 rule 编号，
       编号不在 REVIEW_RULES 里的直接丢弃。

这么分是被实测逼出来的：一开始 6 条全交给模型，它会顺手报
"动线不合理""太赶""花费比预算少太多""每天都有晚餐算重复景点"，
甚至把"未超预算，此项不违规"塞进 violations——每次规划都被打回重排，
等于凭空多花一次生成。算术能判的事就不该交给模型。
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from app.core import llm as llm_runtime
from app.core.json_utils import parse_llm_json
from app.core.prompts import build_review_prompt
from app.core.state import TripState

# 最多重排几次。1 次 ≈ 最坏多花一次生成 + 一次质检；再多对体验帮助很小，
# 反而把"旅行规划"拖成一分钟的等待。
REVIEW_MAX_ROUNDS = 1

# 只需要常识判断的两条。改这里必须同步改 prompts/review.py 的 REVIEW_RULE_TEXT。
REVIEW_RULES = {2, 6}

# 餐饮/住宿/交通这类每天都有的安排，名字相似不算"重复景点"
_NON_SPOT_HINTS = ("餐", "酒店", "入住", "住宿", "抵达", "返程", "出发", "机场", "高铁",
                   "车站", "交通", "休息", "自由活动")

# 模型在 detail 里自证"其实不违规"的措辞：这类条目要丢掉，否则会被当成违规
_NEGATION_HINTS = ("不违规", "不构成", "不违反", "未超过", "未超标", "不算违规",
                   "无问题", "符合要求", "并未")


def city_name(destination: str) -> str:
    """'辽宁省大连市' -> '大连'。判断景点是否在城里用短名更稳。"""
    city = destination or ""
    if "省" in city:
        city = city.split("省")[-1]
    if "市" in city:
        city = city.split("市")[0]
    return city.strip() or destination or "未知"


def _is_spot(name: str) -> bool:
    return bool(name) and not any(hint in name for hint in _NON_SPOT_HINTS)


def _time_start(value: Any) -> Optional[int]:
    """'09:00-10:30' -> 540（当天第几分钟）。解析不出来返回 None。"""
    match = re.match(r"\s*(\d{1,2})\s*[:：]\s*(\d{2})", str(value or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if 0 <= hour < 24 and 0 <= minute < 60:
        return hour * 60 + minute
    return None


def code_review(state: TripState) -> List[str]:
    """规则 1/3/4/5 的确定性校验。返回的每条都带 [规则N] 前缀。"""
    plan = state.get("draft_plan") or []
    want_days = state.get("days") or 3
    budget = state.get("budget") or 0
    issues: List[str] = []

    if len(plan) != want_days:
        issues.append(f"[规则1] 行程只有 {len(plan)} 天，用户要求 {want_days} 天")

    total = 0.0
    for day in plan:
        for act in (day.get("activities") or []):
            try:
                total += float(act.get("cost") or 0)
            except (TypeError, ValueError):
                pass
    if budget and total > budget:
        issues.append(f"[规则3] 活动费用合计 {total:.0f} 元，超过预算 {budget} 元")

    spot_days: Dict[str, set] = {}
    for day in plan:
        day_no = day.get("day")
        acts = day.get("activities") or []
        if not 2 <= len(acts) <= 6:
            issues.append(f"[规则4] 第 {day_no} 天有 {len(acts)} 个活动，应保持在 2-6 个")

        starts = []
        for act in acts:
            start = _time_start(act.get("time"))
            if start is not None:
                starts.append(start)
            name = str(act.get("activity") or "").strip()
            if _is_spot(name):
                spot_days.setdefault(name, set()).add(day_no)

        # 时间重复或倒序：排序后和原顺序不一致就说明有问题
        if len(starts) != len(set(starts)) or starts != sorted(starts):
            issues.append(f"[规则4] 第 {day_no} 天的时间有重复或倒序")

    repeats = [
        f"{name}（第 {'、'.join(str(d) for d in sorted(days))} 天）"
        for name, days in spot_days.items() if len(days) > 1
    ]
    if repeats:
        issues.append("[规则5] 同一景点出现在多天：" + "；".join(repeats))

    return issues


def parse_violations(data: Dict) -> Tuple[List[str], bool, str, List[str]]:
    """把模型输出收敛成"可信的违规项"。

    只保留 rule 在 REVIEW_RULES 里、detail 非空、且没有自证"不违规"的条目。
    实测模型会把"未超预算，此项不违规"也写进 violations，不滤掉就会白白重排。

    conflicts 是"用户约束自相矛盾"的通道：一旦出现，直接放行，
    因为这类问题改多少版行程都解决不了。放行的同时把矛盾原样带回去提示用户。
    """
    raw = data.get("violations")
    if raw is None:
        # 兼容旧格式：直接给 passed / issues
        issues = [str(i).strip() for i in (data.get("issues") or []) if str(i).strip()]
        return issues, not issues, str(data.get("feedback") or "").strip(), []

    issues: List[str] = []
    for item in raw or []:
        if isinstance(item, dict):
            rule, detail = item.get("rule"), str(item.get("detail") or "").strip()
        else:
            rule, detail = item, str(item).strip()
        try:
            rule_no = int(rule)
        except (TypeError, ValueError):
            continue
        if rule_no not in REVIEW_RULES or not detail:
            continue
        if any(hint in detail for hint in _NEGATION_HINTS):
            continue
        issues.append(f"[规则{rule_no}] {detail}")

    conflicts = [str(c).strip() for c in (data.get("conflicts") or []) if str(c).strip()]
    feedback = str(data.get("feedback") or "").strip() or "；".join(issues)

    if conflicts:
        # 约束本身打架：报出来的 violations 多半是这个矛盾的副产品，
        # 一起丢掉，否则会重排一次仍然不过，白烧一轮 token 和十几秒。
        return [], True, feedback, conflicts

    return issues, not issues, feedback, conflicts


def merge_plan_tips(state: TripState, conflicts: List[str]) -> None:
    """把"约束自相矛盾"的提示并进行程 tips。

    tips 会随 trip_plan 推给前端并落库，用户能在行程卡片上直接看到；
    只写进日志的话，他只会觉得系统擅自忽略了他提的要求。
    """
    tips = list(state.get("plan_tips") or [])
    for item in conflicts:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > 80:
            text = text[:78] + "…"
        line = f"⚠️ 你的要求之间有冲突：{text}"
        if line not in tips:
            tips.append(line)
    state["plan_tips"] = tips


async def review_plan_node(state: TripState, config: RunnableConfig = None) -> TripState:
    """自我反思（Evaluator）：拿用户约束当尺子量这一版行程。

    为什么用 llm_extract 而不是 llm：评审的输出是内部批注，绝不能进正文。
    llm_extract 是 streaming=False，再加 internal 标签，api/chat.py 的正文判定
    就不会把它当用户可见内容推给前端（这条规则和景点提取共用）。

    质检自己出错（超时、JSON 解析失败）时**放行**：宁可交付一份可能有瑕疵的行程，
    也不能因为评审挂了让用户啥都拿不到。
    """
    writer = config.get("configurable", {}).get("writer") if config else None
    if writer:
        writer({
            "type": "node_start",
            "node": "质检行程",
            "message": "正在核对行程是否满足约束..."
        })

    passed, issues, feedback, conflicts = True, [], "", []
    days = state.get("draft_plan") or []

    if days:
        # 第一层：算术能判的先判。有硬伤就直接重排，省掉这次 LLM 调用，
        # 也避免模型在数字上"发挥"（它算错过总价，还把"未超预算"当违规报上来）。
        issues = code_review(state)
        if issues:
            feedback = "；".join(issues)
        else:
            # 第二层：只剩城市错位 / 偏好冲突需要常识
            try:
                prompt = build_review_prompt(
                    city=city_name(state.get("destination") or ""),
                    preferences=state.get("preferences"),
                    request=state.get("user_input"),
                    days=days,
                )
                resp = await llm_runtime.llm_extract.ainvoke(
                    [HumanMessage(content=prompt)],
                    config={"tags": ["internal"]},
                )
                content = resp.content
                if isinstance(content, list):
                    content = " ".join(str(item) for item in content)
                issues, _, feedback, conflicts = parse_violations(parse_llm_json(content))
            except Exception as e:
                print(f"⚠️ 质检失败，这一版直接放行: {e}")
                state["review_error"] = f"行程质检失败: {e}"

        if conflicts:
            # 用户的要求自己打架（要求同一天跨两城之类）。这是需求的问题，不是行程的问题，
            # 重排一次也变不出来，直接放行并把矛盾告诉用户。
            issues = []
            merge_plan_tips(state, conflicts)
            state["review_conflicts"] = conflicts
            print(f"🧐 用户约束互相冲突，放行并提示：{conflicts}")

        passed = not issues

    state["review_passed"] = passed
    state["review_issues"] = issues
    state["review_feedback"] = feedback

    if not passed:
        state["review_round"] = (state.get("review_round") or 0) + 1
        print(f"🧐 质检未通过（第 {state['review_round']} 次重排）：{issues}")
    else:
        print("🧐 质检通过")

    state["current_step"] = "review_plan"
    if writer:
        writer({
            "type": "node_end",
            "node": "质检行程",
            "message": "发现问题，正在重排" if not passed else "行程检查通过"
        })
    return state


def route_after_review(state: TripState) -> List[str]:
    """质检之后：不过且还有重排次数就回到 generate_plan，否则去出地图。

    review_round 在 review_plan_node 里已经 +1，所以这里用 > 判断：
    上限 1 次时，第 1 次不过 → round=1，允许重排；第 2 次还不过 → round=2，收工。
    """
    if state.get("review_passed"):
        return ["generate_map"]
    if (state.get("review_round") or 0) > REVIEW_MAX_ROUNDS:
        print("🧐 重排次数已用尽，按这一版交付")
        return ["generate_map"]
    return ["generate_plan"]
