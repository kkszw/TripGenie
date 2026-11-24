# author: szw
"""会话状态（slots）注入的单元测试。

这是「任务状态告诉模型做到哪了」那一层的唯一实现。四条要锁住：

1. 只注入用户真的提供过的字段。budget 在没提时被填成默认 5000，
   state 里分不出「用户说过」和「系统默认」，写出来等于替用户编需求。
2. 位置在历史之后、本轮用户消息之前。它每轮都变，放到最前会打掉整段前缀缓存。
3. 没有目的地就整条不注入，不要留一条只有天数的半截消息。
4. 读 checkpoint 失败降级为空串，不能让整轮对话挂掉。

跑法：
    cd backend && python -m unittest tests.test_slots_context -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import (  # noqa: E402
    AIMessage, HumanMessage, RemoveMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES  # noqa: E402

from app.api.chat import _build_messages, _load_slots_note  # noqa: E402
from app.core.prompts import SLOTS_NOTE_PREFIX, build_slots_note  # noqa: E402


class BuildSlotsNoteTest(unittest.TestCase):
    """拼装规则。"""

    def test_full(self):
        out = build_slots_note({
            "destination": "青岛市", "days": 3, "preferences": ["海鲜", "亲子"],
        })
        self.assertTrue(out.startswith(SLOTS_NOTE_PREFIX))
        self.assertIn("目的地：青岛市", out)
        self.assertIn("计划天数：3 天", out)
        self.assertIn("偏好：海鲜、亲子", out)

    def test_destination_only(self):
        out = build_slots_note({"destination": "大连市"})
        self.assertEqual(out, f"{SLOTS_NOTE_PREFIX}目的地：大连市")

    def test_no_destination_gives_empty(self):
        # 只有天数没有目的地：整条不注入，否则模型会以为某个它不知道的城市已确认
        self.assertEqual(build_slots_note({"days": 3, "preferences": ["海边"]}), "")

    def test_blank_destination_gives_empty(self):
        self.assertEqual(build_slots_note({"destination": "   ", "days": 3}), "")
        self.assertEqual(build_slots_note({}), "")
        self.assertEqual(build_slots_note(None), "")

    def test_budget_never_leaks(self):
        """默认 5000 是系统填的，写出来模型会当真。"""
        out = build_slots_note({
            "destination": "厦门市", "days": 3, "budget": 5000,
        })
        self.assertNotIn("预算", out)
        self.assertNotIn("5000", out)

    def test_start_date_never_leaks(self):
        out = build_slots_note({
            "destination": "厦门市", "start_date": "2026-11-24",
        })
        self.assertNotIn("2026", out)

    def test_preferences_capped_at_five(self):
        prefs = [f"偏好{i}" for i in range(8)]
        out = build_slots_note({"destination": "厦门市", "preferences": prefs})
        self.assertIn("偏好0", out)
        self.assertIn("偏好4", out)
        self.assertNotIn("偏好5", out)

    def test_blank_preferences_filtered(self):
        out = build_slots_note({
            "destination": "厦门市", "preferences": ["", "   ", "小吃"],
        })
        self.assertEqual(out, f"{SLOTS_NOTE_PREFIX}目的地：厦门市；偏好：小吃")


class BuildMessagesTest(unittest.TestCase):
    """位置与清空语义。"""

    HISTORY = [
        {"role": "user", "content": "去青岛玩3天"},
        {"role": "assistant", "content": "好的，我来安排"},
    ]

    def test_slots_note_sits_before_current_user(self):
        note = build_slots_note({"destination": "青岛市", "days": 3})
        out = _build_messages(self.HISTORY, note, "那边有什么好吃的")

        self.assertIsInstance(out[0], RemoveMessage)
        self.assertEqual(out[0].id, REMOVE_ALL_MESSAGES)
        # RemoveMessage + 历史两条 + 状态一条 + 本轮一条
        self.assertEqual(len(out), 5)
        self.assertIsInstance(out[-2], HumanMessage)
        self.assertEqual(out[-2].content, note)
        self.assertEqual(out[-1].content, "那边有什么好吃的")

    def test_no_note_no_empty_message(self):
        out = _build_messages(self.HISTORY, "", "你好")
        self.assertEqual(len(out), 4)  # RemoveMessage + 2 条历史 + 本轮
        self.assertEqual(out[-1].content, "你好")
        # 倒数第二条是历史里的 assistant，不能被占位成空的人类消息
        self.assertIsInstance(out[-2], AIMessage)
        self.assertEqual(out[-2].content, "好的，我来安排")

    def test_roles_mapped(self):
        out = _build_messages(self.HISTORY, "", "hi")
        self.assertIsInstance(out[1], HumanMessage)
        self.assertIsInstance(out[2], AIMessage)


class _FakeSnapshot:
    def __init__(self, values):
        self.values = values


class _FakeWorkflow:
    def __init__(self, snapshot=None, boom=False):
        self.snapshot = snapshot
        self.boom = boom
        self.calls = []

    async def aget_state(self, config):
        self.calls.append(config)
        if self.boom:
            raise RuntimeError("checkpoint 读失败")
        return self.snapshot


class LoadSlotsNoteTest(unittest.IsolatedAsyncioTestCase):
    """checkpoint 读取与降级。"""

    async def test_reads_values(self):
        wf = _FakeWorkflow(_FakeSnapshot({"destination": "大连市", "days": 3}))
        note = await _load_slots_note(wf, "tid-1")
        self.assertIn("目的地：大连市", note)
        self.assertEqual(wf.calls[0]["configurable"]["thread_id"], "tid-1")

    async def test_first_turn_empty_snapshot(self):
        wf = _FakeWorkflow(_FakeSnapshot({}))
        self.assertEqual(await _load_slots_note(wf, "tid-2"), "")

    async def test_none_snapshot(self):
        self.assertEqual(await _load_slots_note(_FakeWorkflow(None), "tid-3"), "")

    async def test_failure_degrades_to_empty(self):
        # 读不到槽位只是少了背景信息，不能让整轮对话失败
        self.assertEqual(await _load_slots_note(_FakeWorkflow(boom=True), "tid-4"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
