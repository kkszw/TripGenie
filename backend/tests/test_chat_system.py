# author: szw
"""chat 分支 system 提示词注入的单元测试（假模型，不联网）。

锁住三件事：

1. 两条面向用户的模型调用都带 system。chat 一次、tool_node 收尾一次；
   漏掉后者，走工具的那一轮用户看到的正文就是没有约束的。
2. system 不写回 state。写回去会被 add_messages 累积、被 RemoveMessage 清掉，
   既浪费历史 token 预算，也让"唯一事实来源"变成两个。
3. system 文本里没有 `{}` 占位符。有占位符就说明有人想往里拼随轮变化的内容，
   那会打掉整段前缀缓存——这是这段文案最容易被后续改动破坏的地方。

跑法：
    cd backend && python -m unittest tests.test_chat_system -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import (  # noqa: E402
    AIMessage, HumanMessage, SystemMessage, ToolMessage,
)

from app.config import settings  # noqa: E402
from app.core import llm as llm_runtime  # noqa: E402
from app.core.nodes.chat import _with_system, chat, tool_node  # noqa: E402
from app.core.prompts import CHAT_SYSTEM_PROMPT  # noqa: E402


class _CapturingLLM:
    """记录收到的 messages，按固定内容作答。"""

    def __init__(self, reply="好"):
        self.reply = reply
        self.captured = []

    async def astream(self, messages, config=None, **kwargs):
        self.captured.append(list(messages))
        yield AIMessage(content=self.reply)

    async def ainvoke(self, messages, config=None, **kwargs):
        self.captured.append(list(messages))
        return AIMessage(content=self.reply)


class _EchoTool:
    async def ainvoke(self, args):
        return "晴，18℃"


async def _noop_ensure_tools():
    """跳过 MCP 子进程启动：ensure_tools 会起两个 uvx，测试里不能真起。"""
    return None


class WithSystemTest(unittest.TestCase):
    """纯函数行为。"""

    def test_prepends_when_absent(self):
        out = _with_system([HumanMessage(content="青岛天气")])
        self.assertEqual(len(out), 2)
        self.assertIsInstance(out[0], SystemMessage)
        self.assertEqual(out[0].content, CHAT_SYSTEM_PROMPT)

    def test_idempotent_when_system_present(self):
        once = _with_system([HumanMessage(content="x")])
        twice = _with_system(once)
        self.assertEqual(len(twice), len(once))
        self.assertEqual(sum(isinstance(m, SystemMessage) for m in twice), 1)

    def test_empty_messages(self):
        out = _with_system([])
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], SystemMessage)

    def test_prompt_has_no_placeholders(self):
        """有占位符就意味着有人想拼动态内容，而那会破坏前缀缓存。"""
        self.assertNotIn("{", CHAT_SYSTEM_PROMPT)
        self.assertNotIn("%s", CHAT_SYSTEM_PROMPT)


class ChatNodeSystemTest(unittest.IsolatedAsyncioTestCase):
    """chat 节点：带 system 调用，但不把 system 写进返回值。"""

    async def test_system_sent_but_not_persisted(self):
        fake = _CapturingLLM("青岛今天晴，18℃")
        state = {"messages": [HumanMessage(content="青岛今天天气怎么样")], "thread_id": "t1"}

        with mock.patch.object(llm_runtime, "ensure_tools", _noop_ensure_tools), \
                mock.patch.object(llm_runtime, "model_with_tools", fake):
            out = await chat(state, None)

        sent = fake.captured[0]
        self.assertIsInstance(sent[0], SystemMessage)
        self.assertIsInstance(sent[1], HumanMessage)
        # 关键：返回值里不能有 SystemMessage，否则会因为 add_messages 逐轮累积
        persisted = out.get("messages") or []
        self.assertFalse(any(isinstance(m, SystemMessage) for m in persisted))
        self.assertEqual(out["response"], "青岛今天晴，18℃")


class ToolNodeSystemTest(unittest.IsolatedAsyncioTestCase):
    """tool_node 的收尾调用同样要带 system，否则用户看到的正文没有约束。"""

    async def test_final_call_carries_system(self):
        call = {"name": "get_weather", "args": {"city": "青岛"}, "id": "call_1"}
        state = {"messages": [
            HumanMessage(content="青岛天气"),
            AIMessage(content="", tool_calls=[call]),
        ]}
        fake = _CapturingLLM("青岛今天晴，18℃，适合出门")

        with mock.patch.object(llm_runtime, "tools_by_name",
                               {"get_weather": _EchoTool()}, create=True), \
                mock.patch.object(llm_runtime, "llm", fake), \
                mock.patch.object(settings, "tool_timeout", 5):
            out = await tool_node(state)

        sent = fake.captured[0]
        self.assertIsInstance(sent[0], SystemMessage)
        self.assertEqual(sent[0].content, CHAT_SYSTEM_PROMPT)
        # 工具结果要作为数据留在消息里，不能被 system 顶掉
        self.assertTrue(any(isinstance(m, ToolMessage) for m in sent))
        self.assertIn("晴", out["response"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
