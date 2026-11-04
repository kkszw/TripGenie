# author: szw
"""槽位抽取与追问话术。

只做文案拼装，不读写 state——需要什么就传什么，方便单独调整措辞。
"""
from datetime import date as dt_date
from typing import List, Optional


def build_format_prompt(user_input: str, known_dest: str, known_days,
                        known_budget, today: Optional[str] = None) -> str:
    """槽位抽取 prompt。已知值要喂回去，否则"改成5天"会被抽成空目的地。"""
    today = today or dt_date.today().isoformat()
    return f"""
        分析用户的旅行需求，提取关键信息。

        已知信息（前几轮已确认，本轮没提到就沿用，不要自行编造）：
        - 目的地：{known_dest or "未提供"}
        - 天数：{known_days or "未提供"}
        - 预算：{known_budget or "未提供"}

        用户本轮输入：{user_input}

        规则：
        1. 只输出用户本轮明确提到的字段；没提到的，destination 填空字符串、days 填 null、budget 填 null。
        2. days 必须是用户明确说过的天数，没提就填 null，不要默认成 3。
        3. destination 用 省名+市名，例如 辽宁省大连市。用户只说了省份（"云南""四川"）
           就填省份本身，不要因为填不出一座具体城市就留空——省份一样能规划。
        4. task_type 判断本轮和上一份行程的关系：
           - "modify"：在改/补充刚才的行程，包括"改成5天""换成便宜点的""对""3天"这类回答；
           - "new"：重新提出一次规划请求，例如"帮我规划个旅行""再给我排一版别的"。
           如果输入里没有任何指向上文行程的线索，一律填 "new"。

        请只返回如下的JSON格式（不要加代码围栏）：
        {{
            "task_type": "new",
            "destination": "",
            "days": null,
            "budget": null,
            "preferences": ["偏好1", "偏好2"],
            "start_date": "{today}"
        }}
        """


def clarify_text(missing: List[str], dest: str = "", days=None) -> str:
    """按缺失的槽位挑一句追问。

    用模板而不是让 LLM 生成：追问场景只有这几种，模板零延迟、零成本、措辞可控；
    让 LLM 生成要多一次调用，还可能把待确认的信息说得更含糊。
    """
    # 情况 0：历史里有目的地，但本轮没提、也不是在补全追问
    # -> 不是"缺信息"，而是"要不要沿用"，措辞要把老答案摆出来让用户确认
    if "confirm_destination" in missing:
        suffix = f"（{days} 天）" if days else ""
        return (f"上次规划的是 {dest}{suffix}，这次还是按它来吗？"
                f"想换城市或改天数，直接告诉我。")

    has_dest = "destination" not in missing
    has_days = "days" not in missing

    if not has_dest and not has_days:
        return "想去哪个城市？大概玩几天？还没定天数的话，我先按 3 天帮你排。"
    if not has_dest:
        return "想去哪个城市？"
    return "打算玩几天？没说我就先按 3 天排，后面随时能改。"
