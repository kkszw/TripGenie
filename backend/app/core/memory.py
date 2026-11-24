# author: szw
"""用户长期档案：读取渲染、抽取、归并落库。

分三层，职责不要混：

    prompts/memory.py     文案、键表、抽取门槛（纯函数，好测）
    core/memory.py        这一层：读库渲染 + 调模型抽取 + 归并落库
    services/database.py  只管 SQL

**这一层刻意不进 LangGraph**。档案是请求级的背景，和 checkpoint 里的会话槽位
是两件事：槽位属于某个 thread，档案属于某个 user。塞进 state 有两个问题——
checkpoint 里存一份会随会话老化，换会话又读不到；而且 state 是节点之间传递的
东西，档案在进图之前就该拼好，不需要任何节点去写它。

成本上的两个决定，改之前先看注释：
  - 抽取是后台任务，不阻塞 SSE。用户等回复时不差这一次调用，但差这 1 秒延迟。
  - 抽取前有关键词门槛（prompts/memory.py 的 looks_memorable），能砍掉八成流量。
"""
import asyncio
from typing import List, Optional

from langchain_core.messages import HumanMessage

from app.config import settings
from app.core.json_utils import parse_llm_json
from app.core.logging import get_logger
from app.core.prompts import (
    MEMORY_KEYS,
    build_memory_prompt,
    looks_memorable,
    render_memory_block,
)
from app.services.database import db

logger = get_logger(__name__)

# 抽取时只看用户消息的前这么多字。请求体上限是 4000 字，全文喂进去只为抽一两条
# 偏好，纯浪费；真正的偏好陈述几乎都在开头。
_EXTRACT_INPUT_CHARS = 800

# 单条 value 的长度上限。模型偶尔会把整句话塞进 value（"我不吃辣也不能吃香菜
# 因为胃不好"），一条就吃掉半个预算，注入进去还挤掉别的约束。
_MAX_VALUE_CHARS = 40

# 后台任务的强引用集合。
#
# 必须留着：asyncio.create_task 返回的 task，如果调用方不保存引用，事件循环
# 之外没有任何东西持有它，可能在跑完之前就被 GC 回收——表现是"抽取偶尔不生效"，
# 比完全没抽取更难排查。done_callback 里移除以避免集合无限增长。
_pending_tasks: set = set()


def render_user_memory(user_id, max_tokens: Optional[int] = None) -> str:
    """把某个用户的长期档案渲染成一条背景消息；没有可用的就返回空串。

    max_tokens <= 0 表示不裁（与 trim_to_token_budget、RAG_CONTEXT_MAX_TOKENS=0
    的约定一致，不另立一套语义）。

    读库失败一律降级为空串：档案只是背景，缺了最多是回答少个参照，
    不该让整轮对话失败——和 api/chat.py 里 _load_slots_note 的处理方式一致。
    """
    if not user_id or str(user_id) == "-":
        return ""
    if not settings.user_memory_enabled:
        return ""

    limit = settings.user_memory_max_tokens if max_tokens is None else max_tokens
    try:
        items = db.get_user_memory(str(user_id))
    except Exception as exc:
        logger.warning("读取长期档案失败，本轮不带记忆", extra={"err": str(exc)})
        return ""

    return render_memory_block(items, limit)


def merge_items(user_id, items, source: str = "chat",
                thread_id: Optional[str] = None) -> int:
    """把抽取结果写进库，返回真正发生变化的条数。

    未知 key 直接丢弃而不是记下来：模型的键名只要飘一次，档案里就多一条
    永远没人读、还占预算的孤儿项。key 是固定的，才有"同 key 覆盖"可言。

    action="clear" 走删除：用户说"我现在能吃辣了"，正确做法是删掉
    diet_spicy，而不是写一条"能吃辣"——后者在同一张表里和别的表述并存时，
    模型无法判断哪条是新的。
    """
    changed = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key not in MEMORY_KEYS:
            logger.debug("丢弃未知记忆键", extra={"key": key})
            continue

        if str(item.get("action") or "set").strip().lower() == "clear":
            if db.delete_user_memory(str(user_id), key):
                changed += 1
                logger.info("记忆已清除", extra={"key": key})
            continue

        value = str(item.get("value") or "").strip()
        if not value or len(value) > _MAX_VALUE_CHARS:
            continue

        if db.upsert_user_memory(
            str(user_id), key, value, MEMORY_KEYS[key][0], source, thread_id
        ):
            changed += 1
            logger.info("记忆已更新", extra={"key": key, "value": value})

    return changed


async def extract(user_id, thread_id, message: str) -> int:
    """跑一次抽取并落库。失败一律吞掉——档案抽不到不影响用户这一轮的回复。"""
    # 延迟 import：ChatOpenAI 在模块导入时就构造，而测试只测渲染/归并逻辑时
    # 不该因为缺少 api_key 就 import 失败。
    from app.core import llm as llm_runtime

    try:
        existing = {item["key"]: item["value"]
                    for item in db.get_user_memory(str(user_id))}
    except Exception as exc:
        logger.warning("读取已有档案失败，按空档案抽取", extra={"err": str(exc)})
        existing = {}

    prompt = build_memory_prompt((message or "")[:_EXTRACT_INPUT_CHARS], existing)
    try:
        response = await llm_runtime.llm_extract.ainvoke([HumanMessage(content=prompt)])
        content = response.content
        if isinstance(content, list):
            content = " ".join(str(part) for part in content)
        data = parse_llm_json(content)
    except Exception as exc:
        logger.warning("长期记忆抽取失败", extra={"err": str(exc)})
        return 0

    try:
        return merge_items(str(user_id), data.get("items"), "chat", thread_id)
    except Exception as exc:
        logger.warning("长期记忆写库失败", extra={"err": str(exc)})
        return 0


async def _safe_extract(user_id: str, thread_id: str, message: str) -> None:
    """后台任务的入口。最外层再兜一次异常：任务里抛出去的异常没人 await，
    只会被 asyncio 记为 "Task exception was never retrieved"，日志里看不出
    是哪个用户、哪一轮。"""
    try:
        await extract(user_id, thread_id, message)
    except Exception as exc:
        logger.warning("长期记忆后台任务异常", extra={"err": str(exc)})


def schedule_extraction(user_id, thread_id, message: str) -> None:
    """在后台排一次抽取。调用方不要 await，回复该发就发。

    只接受**用户消息**：把 assistant 的回复也喂进去，模型会把自己编的
    "您喜欢安静"反抽成用户偏好，几轮之后档案里全是它自己的幻觉，形成自我强化。
    """
    if not settings.user_memory_enabled:
        return
    if not user_id or str(user_id) == "-":
        return
    if not looks_memorable(message):
        return

    task = asyncio.create_task(
        _safe_extract(str(user_id), str(thread_id or ""), message or "")
    )
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


def list_user_memory(user_id) -> List[dict]:
    """给运维/排查用：直接返回库里的原始条目。"""
    if not user_id:
        return []
    return db.get_user_memory(str(user_id))
