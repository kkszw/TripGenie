# author: szw
"""提示词集中出口。

约定：prompts/ 下只放字符串与拼装函数，不做业务判断、不读写 state。
改文案时只需要看这个目录，不用在节点逻辑里翻。
"""
from app.core.prompts.attraction import build_attraction_prompt
from app.core.prompts.classify import CLASSIFY_SYSTEM_PROMPT
from app.core.prompts.plan import PLAN_TEMPLATE, build_plan_prompt
from app.core.prompts.review import CONFLICT_HINT, REVIEW_RULE_TEXT, build_review_prompt
from app.core.prompts.slots import build_format_prompt, clarify_text

__all__ = [
    "CLASSIFY_SYSTEM_PROMPT",
    "PLAN_TEMPLATE",
    "REVIEW_RULE_TEXT",
    "CONFLICT_HINT",
    "build_attraction_prompt",
    "build_format_prompt",
    "build_plan_prompt",
    "build_review_prompt",
    "clarify_text",
]
