# author: szw
"""用户长期档案（跨会话记忆）的单元测试。

六条要锁住的东西：

1. **渲染顺序确定**。同一条消息是缓存前缀的一部分，同一用户每轮必须是同一串
   字节；顺序一抖，历史再长也命中不了（插件价差 50 倍）。
2. **硬约束优先**。预算不够时砍偏好、保忌口和过敏，不能反过来。
3. **key 只认固定枚举**。模型编出来的键直接丢，否则档案会慢慢长满同义孤儿项，
   而且没法用「同 key 覆盖」表达用户改主意。
4. **同值不重复写**。每轮抽同一句「不吃辣」不该让 updated_at 一直跳。
5. **clear 是删除，不是写一条相反的记录**。
6. **注入位置在历史之前**。这是缓存前缀能否成立的前提。

跑法：
    cd backend && python -m unittest tests.test_user_memory -v
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import HumanMessage, RemoveMessage  # noqa: E402
from langgraph.graph.message import REMOVE_ALL_MESSAGES  # noqa: E402

from app.api.chat import _build_messages  # noqa: E402
from app.config import settings  # noqa: E402
from app.core.memory import merge_items, render_user_memory  # noqa: E402
from app.core.prompts import (  # noqa: E402
    MEMORY_PREFIX,
    build_memory_prompt,
    looks_memorable,
    render_memory_block,
)
from app.services.database import DatabaseService  # noqa: E402


def _items():
    return [
        {"key": "diet_spicy", "category": "constraint", "value": "一点辣都不吃"},
        {"key": "diet_allergy", "category": "constraint", "value": "花生过敏"},
        {"key": "pace", "category": "preference", "value": "每天最多两个景点"},
        {"key": "accommodation", "category": "preference", "value": "优先民宿"},
    ]


class RenderMemoryBlockTest(unittest.TestCase):
    """纯拼装：顺序、预算、兜底。"""

    def test_empty(self):
        self.assertEqual(render_memory_block([]), "")
        self.assertEqual(render_memory_block(None), "")
        # 有记录但 value 是空白的，等于没有
        self.assertEqual(render_memory_block([{"key": "pace", "value": "   "}]), "")

    def test_constraint_before_preference(self):
        """输入顺序是乱的，输出必须是硬约束在前。"""
        out = render_memory_block(list(reversed(_items())))
        self.assertTrue(out.startswith(MEMORY_PREFIX))
        self.assertLess(out.index("吃辣"), out.index("节奏"))

    def test_same_input_same_bytes(self):
        """同一份数据渲染两次必须逐字相同——它是缓存前缀。"""
        self.assertEqual(render_memory_block(_items()), render_memory_block(_items()))

    def test_unknown_key_falls_back_to_key(self):
        """模型编的键不该让渲染崩掉，但也只在这里兜底，写库时早就丢了。"""
        out = render_memory_block([{"key": "mystery_key", "value": "随便"}])
        self.assertIn("mystery_key：随便", out)

    def test_budget_drops_preference_not_constraint(self):
        """45 token 只够前缀 + 一条，砍掉的两条必须是偏好。

        留下的那一条由 (category, key) 决定：同类里 diet_allergy 字典序在前，
        所以是「花生过敏」而不是「吃辣」。这不是刻意排的优先级，只是排序必须
        确定——顺序一抖前缀就失效，而它比"哪条先留下"重要得多。
        """
        out = render_memory_block(_items(), max_tokens=45)
        self.assertIn("花生过敏", out)
        self.assertNotIn("住宿", out)
        self.assertNotIn("节奏", out)

    def test_zero_means_no_limit(self):
        """0 = 不裁，与 trim_to_token_budget / RAG_CONTEXT_MAX_TOKENS 的约定一致。"""
        out = render_memory_block(_items(), max_tokens=0)
        for label in ("吃辣", "花生过敏", "节奏", "住宿"):
            self.assertIn(label, out)


class LooksMemorableTest(unittest.TestCase):
    """抽取门槛只负责省钱，不负责正确，所以只测它别把明显该抽的漏了。"""

    def test_hits(self):
        for text in ("我不吃辣", "带两岁孩子出门", "膝盖不好别爬山", "以后都住民宿吧"):
            self.assertTrue(looks_memorable(text), text)

    def test_misses(self):
        for text in ("帮我规划青岛三天", "改天再说", "这个行程不错"):
            self.assertFalse(looks_memorable(text), text)

    def test_empty(self):
        self.assertFalse(looks_memorable(""))
        self.assertFalse(looks_memorable(None))


class MemoryPromptTest(unittest.TestCase):
    """抽取 prompt 的两件事：喂回已有值、禁掉身份信息。"""

    def test_includes_existing_values(self):
        prompt = build_memory_prompt("我不吃辣", {"diet_spicy": "偶尔吃微辣"})
        self.assertIn("偶尔吃微辣", prompt)

    def test_marks_empty_existing(self):
        self.assertIn("暂无", build_memory_prompt("我不吃辣", {}))

    def test_forbids_identity_info(self):
        prompt = build_memory_prompt("我的身份证是 2102...", {})
        self.assertIn("证件号", prompt)

    def test_lists_all_keys(self):
        prompt = build_memory_prompt("随便", {})
        for key in ("diet_spicy", "diet_allergy", "pace", "travel_with"):
            self.assertIn(key, prompt)


class MergeItemsTest(unittest.TestCase):
    """落库规则。用临时库，不碰真实的 sessions.db。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseService(os.path.join(self._tmp.name, "test.db"))
        self._patch = patch("app.core.memory.db", self.db)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_set_and_overwrite(self):
        self.assertEqual(merge_items("u1", [
            {"key": "diet_spicy", "value": "不吃辣", "action": "set"}]), 1)
        # 同值再来一次：不算变化，updated_at 不该跳
        self.assertEqual(merge_items("u1", [
            {"key": "diet_spicy", "value": "不吃辣", "action": "set"}]), 0)
        # 改了才算
        self.assertEqual(merge_items("u1", [
            {"key": "diet_spicy", "value": "一点辣都不吃"}]), 1)
        rows = self.db.get_user_memory("u1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], "一点辣都不吃")

    def test_unknown_key_dropped(self):
        self.assertEqual(merge_items("u1", [{"key": "no_such_key", "value": "x"}]), 0)
        self.assertEqual(self.db.get_user_memory("u1"), [])

    def test_clear_deletes_instead_of_rewriting(self):
        merge_items("u1", [{"key": "diet_spicy", "value": "不吃辣"}])
        self.assertEqual(merge_items("u1", [
            {"key": "diet_spicy", "action": "clear"}]), 1)
        self.assertEqual(self.db.get_user_memory("u1"), [])

    def test_clear_absent_key_is_noop(self):
        self.assertEqual(merge_items("u1", [
            {"key": "diet_spicy", "action": "clear"}]), 0)

    def test_bad_values_dropped(self):
        self.assertEqual(merge_items("u1", [
            {"key": "pace", "value": "   "},
            {"key": "pace", "value": "很长" * 40},
            {"value": "没有 key"},
            "not-a-dict",
        ]), 0)
        self.assertEqual(self.db.get_user_memory("u1"), [])

    def test_category_comes_from_key_table(self):
        """category 不接受模型给的值，一律按键表定，否则预算裁剪会失灵。"""
        merge_items("u1", [{"key": "diet_allergy", "category": "preference",
                            "value": "花生过敏"}])
        self.assertEqual(self.db.get_user_memory("u1")[0]["category"], "constraint")

    def test_order_is_constraint_first(self):
        merge_items("u1", [
            {"key": "pace", "value": "慢一点"},
            {"key": "diet_spicy", "value": "不吃辣"},
        ])
        keys = [row["key"] for row in self.db.get_user_memory("u1")]
        self.assertEqual(keys, ["diet_spicy", "pace"])

    def test_users_are_isolated(self):
        merge_items("u1", [{"key": "pace", "value": "慢一点"}])
        self.assertEqual(self.db.get_user_memory("u2"), [])


class RenderUserMemoryTest(unittest.TestCase):
    """读取侧：开关、降级、预算。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseService(os.path.join(self._tmp.name, "test.db"))
        self._patch = patch("app.core.memory.db", self.db)
        self._patch.start()
        self.db.upsert_user_memory("u1", "diet_spicy", "不吃辣", "constraint")

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_renders_from_db(self):
        out = render_user_memory("u1")
        self.assertIn("不吃辣", out)

    def test_other_user_gets_nothing(self):
        self.assertEqual(render_user_memory("u2"), "")

    def test_unknown_or_dash_user(self):
        self.assertEqual(render_user_memory(None), "")
        self.assertEqual(render_user_memory("-"), "")

    def test_disabled_switch(self):
        with patch.object(settings, "user_memory_enabled", False):
            self.assertEqual(render_user_memory("u1"), "")

    def test_db_failure_degrades_to_empty(self):
        """读库炸了也只是少一段背景，不能让整轮对话挂掉。"""
        with patch.object(self.db, "get_user_memory", side_effect=RuntimeError("boom")):
            self.assertEqual(render_user_memory("u1"), "")


class BuildMessagesTest(unittest.TestCase):
    """注入位置：档案在历史之前，会话状态在历史之后。"""

    def test_memory_before_history(self):
        messages = _build_messages(
            [{"role": "user", "content": "上一轮问题"},
             {"role": "assistant", "content": "上一轮回答"}],
            "【当前会话状态】目的地：青岛市",
            "这轮问题",
            "【长期档案】吃辣：不吃辣",
        )
        self.assertIsInstance(messages[0], RemoveMessage)
        self.assertEqual(messages[0].id, REMOVE_ALL_MESSAGES)
        self.assertIsInstance(messages[1], HumanMessage)
        self.assertIn("长期档案", messages[1].content)
        # 历史紧随其后
        self.assertEqual(messages[2].content, "上一轮问题")
        self.assertEqual(messages[3].content, "上一轮回答")
        # 会话状态仍在历史之后、本轮消息之前
        self.assertIn("当前会话状态", messages[4].content)
        self.assertEqual(messages[5].content, "这轮问题")

    def test_without_memory_keeps_old_layout(self):
        """旧调用方（测试、未来的复用方）不传档案时，布局不变。"""
        messages = _build_messages(
            [{"role": "user", "content": "q"}], "", "这轮问题")
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[1].content, "q")
        self.assertEqual(messages[2].content, "这轮问题")

    def test_no_duplicate_when_history_already_has_note(self):
        """档案为空时不能凭空多出一条空消息。"""
        messages = _build_messages([], "", "这轮问题", "")
        self.assertEqual(len(messages), 2)


if __name__ == "__main__":
    unittest.main()
