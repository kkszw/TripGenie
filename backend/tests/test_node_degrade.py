# author: szw
"""节点降级路径的单元测试（假模型注入，不联网）。

线上最怕的不是"模型答得不好"，而是"模型答得不规范时直接抛异常"。generate_plan
和 format_input 都靠 parse_llm_json 解析模型输出，解析失败会走 except 分支：
把错误写进 state、把模型原文塞回 response，让用户至少能看到内容、而不是白屏。

这条分支此前从未被主动触发过——写了但不知道对不对。这里用一个假 LLM 吐出
各种坏格式，确认节点不抛异常、状态字段符合预期。

跑法：
    cd backend && python -m unittest tests.test_node_degrade -v
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import llm as llm_runtime  # noqa: E402
from app.core.nodes.plan import generate_plan_node  # noqa: E402
from app.core.nodes.slots import format_input  # noqa: E402

BAD_JSON = '好的，我给您排了行程：{"days": [{"day": 1, "attractions": ['
GOOD_PLAN = json.dumps(
    {
        "days": [{"day": 1, "attractions": [{"name": "栈桥"}, {"name": "八大关"}]}],
        "tips": ["海边风大，带件外套"],
    },
    ensure_ascii=False,
)


class _Message:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """只实现节点用到的两个方法：astream（骨架/流式）与 ainvoke（内部抽取）。"""

    def __init__(self, text, pieces=4):
        self.text = text
        self.pieces = pieces

    async def astream(self, messages, config=None, **kwargs):
        size = max(1, len(self.text) // self.pieces)
        for start in range(0, len(self.text), size):
            yield _Message(self.text[start:start + size])

    async def ainvoke(self, messages, config=None, **kwargs):
        return _Message(self.text)


def _plan_state(**overrides):
    state = {
        "thread_id": "test-thread",
        "destination": "青岛",
        "days": 3,
        "budget": 3000,
        "preferences": ["海鲜"],
        "start_date": "2026-11-05",
    }
    state.update(overrides)
    return state


class GeneratePlanDegradeTest(unittest.IsolatedAsyncioTestCase):
    async def test_bad_json_does_not_raise(self):
        """模型吐半截 JSON 时，节点必须自己兜住，不能把异常抛给 SSE 层。"""
        state = _plan_state()
        with mock.patch.object(llm_runtime, "llm", _FakeLLM(BAD_JSON)):
            out = await generate_plan_node(state, None)

        self.assertIn("生成行程失败", out["error"])
        self.assertEqual(out["current_step"], "generate_plan")

    async def test_bad_json_keeps_raw_text_visible(self):
        """兜底的关键：用户要能看到模型说了什么，而不是一片空白。"""
        state = _plan_state()
        with mock.patch.object(llm_runtime, "llm", _FakeLLM(BAD_JSON)):
            out = await generate_plan_node(state, None)

        self.assertEqual(out["response"], BAD_JSON)
        self.assertNotIn("draft_plan", out)

    async def test_good_json_fills_plan(self):
        """对照组：假模型本身没测歪，正常 JSON 能解析成行程。"""
        state = _plan_state()
        with mock.patch.object(llm_runtime, "llm", _FakeLLM(GOOD_PLAN)):
            out = await generate_plan_node(state, None)

        self.assertNotIn("error", out)
        self.assertEqual(len(out["draft_plan"]), 1)
        self.assertEqual(out["plan_tips"], ["海边风大，带件外套"])
        self.assertIn("栈桥", out["response"])

    async def test_stream_is_forwarded_to_writer(self):
        """SSE 依赖 writer 逐块转发，不能等整段生成完才吐。"""
        chunks = []
        config = {"configurable": {"writer": chunks.append}}
        state = _plan_state()

        with mock.patch.object(llm_runtime, "llm", _FakeLLM(GOOD_PLAN)):
            await generate_plan_node(state, config)

        text_chunks = [c["content"] for c in chunks if c.get("type") == "chunk"]
        self.assertGreater(len(text_chunks), 1, "应分多块流式输出")
        self.assertEqual("".join(text_chunks), GOOD_PLAN)
        self.assertTrue(any(c.get("type") == "node_start" for c in chunks))
        self.assertTrue(any(c.get("type") == "node_end" for c in chunks))


class FormatInputDegradeTest(unittest.IsolatedAsyncioTestCase):
    async def test_bad_json_keeps_previous_slots(self):
        """解析失败时不能把上一轮的目的地/天数冲掉，否则用户说"改成5天"就丢了三亚。"""
        state = {
            "thread_id": "t",
            "user_input": "改成5天",
            "destination": "三亚",
            "days": 3,
            "budget": 5000,
        }
        with mock.patch.object(llm_runtime, "llm", _FakeLLM("抱歉，我不太确定。")):
            out = await format_input(state, None)

        self.assertEqual(out["error"], "解析用户输入失败")
        self.assertEqual(out["destination"], "三亚")
        self.assertEqual(out["days"], 3)
        self.assertEqual(out["budget"], 5000)

    async def test_good_json_updates_explicit_fields_only(self):
        """LLM 返回空 destination 时视为"本轮没提"，只覆盖它明确给了的字段。"""
        state = {
            "thread_id": "t",
            "user_input": "改成5天，预算加到8000",
            "destination": "三亚",
            "days": 3,
            "budget": 5000,
        }
        payload = json.dumps(
            {"destination": "", "days": 5, "budget": "8000元", "task_type": "modify"},
            ensure_ascii=False,
        )
        with mock.patch.object(llm_runtime, "llm", _FakeLLM(payload)):
            out = await format_input(state, None)

        self.assertEqual(out["destination"], "三亚", "本轮没提目的地，不能清空")
        self.assertEqual(out["days"], 5)
        self.assertEqual(out["budget"], 8000, "「8000元」要能转成数字")

    async def test_new_destination_replaces_old(self):
        state = {
            "thread_id": "t",
            "user_input": "我想去青岛玩4天",
            "destination": "三亚",
            "days": 3,
            "budget": 5000,
        }
        payload = json.dumps({"destination": "青岛", "days": 4, "task_type": "new"}, ensure_ascii=False)
        with mock.patch.object(llm_runtime, "llm", _FakeLLM(payload)):
            out = await format_input(state, None)

        self.assertEqual(out["destination"], "青岛")
        self.assertEqual(out["days"], 4)
        self.assertEqual(out["current_step"], "format_input")


if __name__ == "__main__":
    unittest.main(verbosity=2)
