# author: szw
"""会话消息分页：游标正确性 + 接口层的兜底与截断提示。

这里钉的核心是"翻完所有页之后，拿到的消息既不重复也不遗漏"——分页最难的
不是单页对不对，而是跨页边界。用临时库跑，不碰真实 sessions.db。

跑法：
    cd backend && python -m unittest tests.test_history_paging -v
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from app.api import history as history_api  # noqa: E402
from app.services.database import DatabaseService  # noqa: E402

THREAD = "t1"


class DbFixture:
    """给两个测试类共用的夹具（故意不继承 TestCase，否则会被重复收集）。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db = DatabaseService(db_path=os.path.join(self.tmpdir.name, "test.db"))
        self.db.create_session(THREAD, "u1", "测试会话")
        self.db.create_session("other", "u1", "另一个会话")

    def seed(self, count: int) -> None:
        """写入 count 轮问答（2*count 条消息）。"""
        for i in range(count):
            self.db.add_message(THREAD, "user", f"问题{i}")
            self.db.add_message(THREAD, "assistant", f"回答{i}")


class TestGetMessagesPage(DbFixture, unittest.TestCase):
    def test_first_page_is_the_latest_in_ascending_order(self):
        self.seed(4)  # 8 条消息
        page = self.db.get_messages_page(THREAD, limit=3)

        # 拿的是最新三条，且按时间正序给前端（前端直接 map，不用再 sort）
        self.assertEqual([m["content"] for m in page["messages"]],
                         ["回答2", "问题3", "回答3"])
        self.assertTrue(page["has_more"])

    def test_walk_backwards_covers_everything_once(self):
        """翻完所有页 = 全量，顺序一致、无重复。"""
        self.seed(5)
        everything = [m["id"] for m in self.db.get_messages(THREAD, limit=100)]

        collected, before, pages = [], None, 0
        while True:
            page = self.db.get_messages_page(THREAD, limit=3, before_id=before)
            collected = [m["id"] for m in page["messages"]] + collected
            pages += 1
            if not page["has_more"]:
                break
            before = page["messages"][0]["id"]

        self.assertEqual(collected, everything)
        self.assertEqual(len(collected), len(set(collected)))
        self.assertEqual(pages, 4)  # 10 条 / 每页 3 条

    def test_exact_multiple_does_not_report_more(self):
        """刚好整除时不能多报一页空的——多取一条就是为这个。"""
        self.seed(3)  # 6 条
        first = self.db.get_messages_page(THREAD, limit=3)
        self.assertTrue(first["has_more"])

        second = self.db.get_messages_page(THREAD, limit=3,
                                          before_id=first["messages"][0]["id"])
        self.assertEqual(len(second["messages"]), 3)
        self.assertFalse(second["has_more"])

    def test_before_oldest_returns_empty(self):
        self.seed(1)  # 2 条
        oldest = self.db.get_messages_page(THREAD, limit=10)["messages"][0]["id"]
        page = self.db.get_messages_page(THREAD, limit=10, before_id=oldest)
        self.assertEqual(page["messages"], [])
        self.assertFalse(page["has_more"])

    def test_sessions_do_not_mix(self):
        self.seed(2)
        self.db.add_message("other", "user", "别人的问题")
        page = self.db.get_messages_page("other", limit=10)
        self.assertEqual([m["content"] for m in page["messages"]], ["别人的问题"])

    def test_empty_session(self):
        page = self.db.get_messages_page(THREAD, limit=10)
        self.assertEqual(page["messages"], [])
        self.assertFalse(page["has_more"])

    def test_limit_zero_does_not_return_empty_page(self):
        """limit=0 会让 LIMIT 0 返回空页，看起来像"这个会话没消息"。"""
        self.seed(1)
        page = self.db.get_messages_page(THREAD, limit=0)
        self.assertEqual(len(page["messages"]), 1)


class TestHistoryEndpoint(DbFixture, unittest.IsolatedAsyncioTestCase):
    """接口层：参数兜底 + has_more/next_before 的交接。"""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(history_api, "db", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_returns_cursor_for_older_page(self):
        self.seed(3)  # 6 条
        page = await history_api.get_session(THREAD, limit=2, before=None,
                                            current_user={"id": "u1"})
        self.assertEqual(len(page["messages"]), 2)
        self.assertTrue(page["has_more"])
        self.assertEqual(page["next_before"], page["messages"][0]["id"])

        older = await history_api.get_session(THREAD, limit=2,
                                              before=page["next_before"],
                                              current_user={"id": "u1"})
        # 两页之间没有重叠：上一页最早那条不会在下一页里再出现
        self.assertLess(older["messages"][-1]["id"], page["messages"][0]["id"])

    async def test_last_page_has_no_cursor(self):
        self.seed(1)
        data = await history_api.get_session(THREAD, limit=50, before=None,
                                             current_user={"id": "u1"})
        self.assertFalse(data["has_more"])
        self.assertIsNone(data["next_before"])

    async def test_limit_is_clamped(self):
        self.seed(2)
        data = await history_api.get_session(THREAD, limit=99999, before=None,
                                             current_user={"id": "u1"})
        self.assertEqual(len(data["messages"]), 4)  # 只有 4 条，说明没被拒

        data = await history_api.get_session(THREAD, limit=0, before=None,
                                             current_user={"id": "u1"})
        self.assertEqual(len(data["messages"]), 1)  # 下限 1，不是空页

    async def test_other_users_session_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            await history_api.get_session(THREAD, limit=50, before=None,
                                          current_user={"id": "u2"})
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
