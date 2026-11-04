# author: szw
"""行程质检的提示词。

这里只放"让模型看什么"，不看哪几条规则、怎么过滤输出——那些在 nodes/review.py。
两边的规则编号必须对得上，改规则时一起看。
"""
import json

# 只需要常识判断的两条；算术能判的（天数/预算/时段/重复景点）在程序里做完了。
REVIEW_RULE_TEXT = """
2. 城市错位：出现明显不在{city}的景点（例如大连的行程里排了三亚的景点）。
6. 偏好冲突：偏好里写了"亲子"，却出现明显不适合的安排（酒吧、极限运动、深夜活动）。
"""

# 用户约束本身自相矛盾时（要求一天跨两城、天数明显排不下指定路线），
# 这不是行程的错：重排多少次都满足不了，只会白跑一次生成。
# 这类情况单独走 conflicts 通道——直接放行，并把矛盾提示给用户。
CONFLICT_HINT = """
例外（很重要）：如果问题的根子在用户的要求自相矛盾（例如要求"第二天去另一座城市"、
同一天既要在一个城市又要出现在另一个城市、给的天数明显排不下指定的多个远方地点），
那就不是行程写错了。不要把它写进 violations，改写进 conflicts，说明矛盾在哪里。
"""


def build_review_prompt(city: str, preferences, request: str, days) -> str:
    """质检 prompt。用户原始需求要带上：否则模型无从判断"串城"是行程写错还是用户自己要求的。"""
    prefs = "、".join(preferences or []) or "无特别偏好"
    request = str(request or "").strip()[:200] or "（未记录）"
    rules = REVIEW_RULE_TEXT.format(city=city)
    return (
        "你是行程质检员。下面是一份旅行行程，请只对照 2 条规则检查。\n\n"
        f"用户原始需求：{request}\n"
        f"用户约束：\n- 目的地：{city}\n- 偏好：{prefs}\n\n"
        f"行程（JSON 数组，每个元素是一天）：\n{json.dumps(days, ensure_ascii=False)}\n\n"
        f"只有这 2 条算违规，rule 必须原样引用编号：{rules}\n"
        f"{CONFLICT_HINT}\n"
        "天数、预算、时段、重复景点已经由程序校验过，不要重复检查，也不要评论。\n"
        "不要评价：景点搭配是否合理、动线/路程赶不赶、值不值得去、时间是否“太满”、"
        "描述写得好不好、能不能更丰富。\n\n"
        "只输出 JSON（不要代码围栏，不要解释）：\n"
        '{"violations": [{"rule": 2, "detail": "具体哪里不符"}], '
        '"conflicts": ["用户要求之间的矛盾，没有就留空数组"], '
        '"feedback": "给规划师的具体修改指令"}\n'
        "只写确实违规的条目；检查后没问题的规则不要出现，更不要写“此项不违规”。"
        "什么都没发现时必须输出 "
        '{"violations": [], "conflicts": [], "feedback": ""}。'
    )
