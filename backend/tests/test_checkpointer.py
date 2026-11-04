# author: szw
"""会话检查点（checkpointer.py）的单元测试。

这文件只有 26 行，但它出问题时的现象是"历史对话莫名消失"，从现象倒推回代码
要花很久。这里钉两件事：

  1. 落库路径必须相对 backend/app 解析，与进程启动目录无关。
     以前直接把相对路径交给 aiosqlite，从仓库根 / backend / IDE 三种方式启动
     会在三个不同位置各生成一个 sessions.db，状态互相看不见——用户看到的就是
     "刚聊过的行程不见了"；
  2. 单例只建一次，且只落一个文件。

测试全程在临时目录里跑，不碰 backend/app/data/checkpoints/。

跑法：
    cd backend && python -m unittest tests.test_checkpointer -v
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import checkpointer as ck  # noqa: E402
from app.utils.path_tool import get_absolute_path  # noqa: E402


class CheckpointPathTest(unittest.TestCase):
    def test_path_is_anchored_at_app_directory(self):
        """无论从哪个 cwd 启动，相对路径都要锚定在 backend/app 上。"""
        path = get_absolute_path("data/checkpoints/sessions.db")
        self.assertTrue(os.path.isabs(path))
        self.assertEqual(os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path)))),
                         "app")
        self.assertTrue(path.replace("\\", "/").endswith("backend/app/data/checkpoints/sessions.db"))

    def test_path_does_not_depend_on_cwd(self):
        original = os.getcwd()
        try:
            os.chdir(tempfile.gettempdir())
            from_temp = get_absolute_path("sessions.db")
        finally:
            os.chdir(original)
        self.assertEqual(from_temp, get_absolute_path("sessions.db"))
        self.assertTrue(from_temp.replace("\\", "/").endswith("backend/app/sessions.db"))


class CheckpointerSingletonTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="tg-ckpt-")
        # 故意多一层不存在的子目录：checkpointer 负责 mkdir -p
        self.db_path = os.path.join(self.tmp_dir, "nested", "sessions.db")
        ck._checkpointer = None

    async def asyncTearDown(self):
        if ck._checkpointer is not None:
            await ck._checkpointer.conn.close()
            ck._checkpointer = None
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _db_files(self):
        found = []
        for root, _dirs, files in os.walk(self.tmp_dir):
            found.extend(os.path.join(root, name) for name in files if name.endswith(".db"))
        return found

    async def test_singleton_and_single_file(self):
        with mock.patch.object(ck, "get_absolute_path", return_value=self.db_path):
            first = await ck.get_checkpointer()
            second = await ck.get_checkpointer()

        self.assertIs(first, second, "重复调用必须复用同一个 saver")
        self.assertEqual(self._db_files(), [self.db_path],
                         "只应生成一个数据库文件（多出来的就是那个多实例 bug）")

    async def test_schema_is_created(self):
        with mock.patch.object(ck, "get_absolute_path", return_value=self.db_path):
            saver = await ck.get_checkpointer()

        cursor = await saver.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in await cursor.fetchall()}
        await cursor.close()
        self.assertIn("checkpoints", tables)
        self.assertIn("writes", tables)

    async def test_parent_directory_is_created(self):
        """数据库目录不存在时自己建，不能等 aiosqlite 抛 unable to open database file。"""
        with mock.patch.object(ck, "get_absolute_path", return_value=self.db_path):
            await ck.get_checkpointer()
        self.assertTrue(os.path.isdir(os.path.dirname(self.db_path)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
