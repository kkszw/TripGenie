# author: szw
"""用户长期档案（跨会话记忆）的文案、键表与抽取门槛。

和本目录其他 prompt 一样，这里只有字符串和纯拼装，不读数据库、不读写 state。

三条硬约束，改文案时不要破坏：

1. **key 必须是固定枚举，不能由模型自由生成**。自由生成会沉淀成「不吃辣」
   「不喜欢辣」「忌辣」三条同义项——注入时既费 token，又互相矛盾，而且
   没法用「同 key 覆盖」来表达用户改主意。想加新类别就改 MEMORY_KEYS，
   这点摩擦是有意的。

2. **只记跨会话仍然成立的结论**。本次行程的预算、天数、出发日期属于会话槽位
   （state.budget / state.days），记进档案会和会话状态打架：用户说「这次预算
   3000」，档案里却留着「消费风格：穷游」，规划时两个信号互相拉扯。

3. **不记身份信息**。证件号、手机号、住址、支付信息在抽取 prompt 里显式禁止。
   旅行场景很容易把「身份证号发给酒店」这类内容顺进来。

渲染出来的那一条消息会插在 system 之后、对话历史之前（见 api/chat.py 的
_build_messages）。位置和顺序都是刻意的：它是缓存前缀的一部分，同一用户
每轮必须是**同一串字节**，否则前缀失效、命中率掉下来（命中与未命中的输入价差
50 倍，见 core/metrics.py 的计价）。
"""
from typing import Dict, List

from app.utils.token_tool import estimate_tokens

# 档案消息的前缀。与 SLOTS_NOTE_PREFIX 并列，两者不能混用：
#   - 本前缀 = 用户级、跨会话、可能过时；
#   - SLOTS_NOTE_PREFIX = 会话级、本轮需求、以此为准。
# 前缀里自带「可能过时、冲突以本轮为准」，是为了不必改 CHAT_SYSTEM_PROMPT——
# system 一改，所有已有会话的缓存前缀全部失效，那是一次性但很贵的代价。
MEMORY_PREFIX = "【长期档案｜系统记录，可能过时，与本轮说法冲突时以本轮为准】"

# key -> (category, 给模型看的中文标签)
# category 只有两档：constraint 是不可妥协的硬约束（优先级更高，预算不够时
# 先砍 preference 保住它），preference 是偏好。
MEMORY_KEYS: Dict[str, tuple] = {
    # ---- 硬约束 ----
    "diet_spicy": ("constraint", "吃辣"),
    "diet_allergy": ("constraint", "食物过敏"),
    "diet_other": ("constraint", "其他饮食要求"),
    "mobility": ("constraint", "体力与行动"),
    "travel_with": ("constraint", "同行人"),
    # ---- 偏好 ----
    "pace": ("preference", "行程节奏"),
    "accommodation": ("preference", "住宿偏好"),
    "budget_style": ("preference", "消费风格"),
    "interest": ("preference", "兴趣主题"),
    "avoid": ("preference", "明确回避"),
    "transport": ("preference", "交通偏好"),
}

# 抽取门槛。只有本轮用户消息命中其中之一，才真的发那次抽取调用。
#
# 为什么要有这道门槛：绝大多数话（"帮我看看天气""改成5天"）里没有可长期记的
# 信息，每轮都抽是纯浪费。命中后仍可能抽出空结果——门槛只负责省钱，
# 不负责保证正确，所以宁宽勿严。
MEMORY_HINTS = (
    # 饮食
    "不吃", "不能吃", "过敏", "忌口", "素食", "清真", "吃素", "忌辣", "辣",
    # 态度
    "不喜欢", "讨厌", "受不了", "怕", "晕车", "晕船",
    # 时间指向：这类词几乎只出现在"以后也用得上"的表述里
    "记住", "以后", "下次", "今后", "每次", "总是", "一直",
    # 同行人
    # 「孩子」不能只靠「带孩子」覆盖：实测「带两岁孩子出门」不含「带孩子」三个连续
    # 字，这类说法在真实对话里很常见，漏了就永远抽不到同行人。
    "带孩子", "带娃", "孩子", "小孩", "儿童", "小朋友", "婴儿",
    "老人", "孕妇", "宠物", "亲子", "一家",
    # 体力
    "膝盖", "腿脚", "体力", "走不动", "无障碍",
    # 消费与交通
    "穷游", "省钱", "预算紧", "消费", "自驾", "不坐飞机", "高铁",
    # 节奏
    "节奏", "太赶", "慢一点", "轻松点",
)


def looks_memorable(text: str) -> bool:
    """这句话值不值得跑一次抽取。纯省钱用的粗筛，不做语义判断。"""
    text = text or ""
    return any(hint in text for hint in MEMORY_HINTS)


def build_memory_prompt(user_message: str, existing: Dict[str, str]) -> str:
    """抽取 prompt。

    existing 必须喂回去：模型不知道已有值时会输出「不吃辣」，把用户原来更具体的
    「一点辣都不吃，包括麻辣香锅」覆盖成一句更模糊的话。喂了已有值，它才会
    输出合并后的完整表述。

    要求「只输出 JSON」而不是用 with_structured_output：这是一次后台短调用，
    多套一层 function_calling 只会让输出更容易跑偏，且 parse_llm_json 已经能
    容忍代码围栏。
    """
    keys_desc = "\n".join(
        f"- {key}（{'硬约束' if category == 'constraint' else '偏好'}）：{label}"
        for key, (category, label) in MEMORY_KEYS.items()
    )
    if existing:
        existing_desc = "\n".join(f"- {key}：{value}" for key, value in existing.items())
    else:
        existing_desc = "（暂无）"

    return f"""从用户这句话里，提取值得**长期记住**的个人信息。

用户的这句话：{user_message}

已有档案（不要丢信息，需要改就输出合并后的完整新值）：
{existing_desc}

可用的键（只能从这里选，key 必须逐字一致）：
{keys_desc}

规则：
1. 只提取「以后规划行程还用得上」的稳定事实。本次行程的天数、预算、出发日期、
   具体景点都不要提——那些是本次需求，不是长期特征。
2. 不提取身份信息：证件号、手机号、住址、单位、支付信息一律忽略。
3. 用户明确否定之前的偏好时，用 action="clear" 删除该键（例如"我现在能吃辣了"
   对应 diet_spicy 的 clear）。
4. 没有值得记的内容就返回空数组，不要为了有输出而硬凑。
5. value 只写结论本身，不要带标签词。
   反例：「行程节奏：行程别太赶」——标签已经由 key 表达，重复一遍是纯浪费；
   正例：「宽松，每天最多两个景点」。

只返回如下 JSON（不要代码围栏、不要解释）：
{{"items": [{{"key": "diet_spicy", "value": "一点辣都不吃", "action": "set"}}]}}
"""


def render_memory_block(items: List[dict], max_tokens: int = 0) -> str:
    """把档案条目渲染成一条背景消息；没有可用条目时返回空串。

    顺序在这里再稳定排一次（数据库已经按 category, key 排过）：函数可能被
    别处直接调用，而"同一用户每轮同一串字节"是缓存前缀的硬要求，不能只靠
    调用方守规矩。

    预算裁剪按整条来，不切半句：「吃辣：一点辣都」这种残句比不放更糟，
    模型会把它当成"用户能接受一点辣"。第一条就超预算也收下——一条超长记忆
    仍然比空档案有用。
    """
    ordered = sorted(
        (item for item in (items or []) if str(item.get("value") or "").strip()),
        key=lambda item: (
            0 if item.get("category") == "constraint" else 1,
            str(item.get("key") or ""),
        ),
    )
    if not ordered:
        return ""

    parts: List[str] = []
    used = estimate_tokens(MEMORY_PREFIX)
    for item in ordered:
        key = str(item.get("key") or "")
        label = MEMORY_KEYS.get(key, (None, key))[1]
        part = f"{label}：{str(item.get('value')).strip()}"
        # +1 是连接用的分号
        cost = estimate_tokens(part) + 1
        if parts and max_tokens > 0 and used + cost > max_tokens:
            break
        parts.append(part)
        used += cost

    return MEMORY_PREFIX + "；".join(parts)
