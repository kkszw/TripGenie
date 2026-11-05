# author: szw
"""登录认证（security.py）的单元测试。

这块写错的后果是「谁都能进」或者「谁都进不来」，而且不会有任何报错提示——
只有用户来投诉。所以三类分支都要钉住：

  1. 密码：bcrypt 只吃前 72 字节（本环境的 bcrypt 5.x 遇到超长密码直接抛
     ValueError），所以密码先 sha256+base64 压成 44 字节再交给它。如果哪天有人
     觉得这一步多余把它删了，长密码会 500、且 72 字节相同前缀的密码能互相登进去；
  2. 令牌：过期、被篡改、换密钥签、alg=none 的无签名令牌，全部必须解析失败；
  3. 依赖注入：无凭证 / 空凭证 / 坏令牌 / 用户已删，四种情况都得是 401 而不是 500。

注意这里**不导入 app.services.database**：那个模块在 import 时就会建表并连
sessions.db，测试不该动生产库。get_current_user 是函数内延迟导入，所以在
sys.modules 里放一个替身就能拦住。

跑法：
    cd backend && python -m unittest tests.test_security -v
"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.security import HTTPAuthorizationCredentials  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.security import (  # noqa: E402
    _JWT_SECRET,
    create_access_token,
    decode_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

LONG_PASSWORD = "a" * 1000          # 远超 bcrypt 的 72 字节上限
BOUNDARY_72 = "a" * 72
BOUNDARY_73 = "a" * 73


class _FakeDB:
    """database 模块的替身：只实现 get_current_user 用到的那一个方法。"""

    def __init__(self, users):
        self._users = users

    def get_user_by_id(self, user_id):
        return self._users.get(user_id)


def _fake_database(users):
    module = types.ModuleType("app.services.database")
    module.db = _FakeDB(users)
    return module


def _bearer(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


class PasswordTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # bcrypt 每算一次约 0.2s，集中算一次复用，别在每个用例里重算
        cls.hash_short = hash_password("secret-123")
        cls.hash_long = hash_password(LONG_PASSWORD)
        cls.hash_72 = hash_password(BOUNDARY_72)
        cls.hash_73 = hash_password(BOUNDARY_73)

    def test_hash_is_bcrypt_format(self):
        self.assertTrue(self.hash_short.startswith("$2"))

    def test_roundtrip(self):
        self.assertTrue(verify_password("secret-123", self.hash_short))

    def test_wrong_password(self):
        self.assertFalse(verify_password("secret-124", self.hash_short))

    def test_salt_makes_hashes_differ(self):
        self.assertNotEqual(hash_password("same"), hash_password("same"))

    def test_long_password_is_supported(self):
        """1000 字符：直接交给 bcrypt 5.x 会抛 ValueError，prehash 之后才可用。"""
        self.assertTrue(verify_password(LONG_PASSWORD, self.hash_long))
        self.assertFalse(verify_password(LONG_PASSWORD[:-1], self.hash_long))

    def test_72_byte_boundary_is_not_truncated(self):
        """如果 prehash 被删掉，这两个断言会变成 True / 抛错——那意味着
        「密码前 72 字节一样就能登录」，属于越权。"""
        self.assertFalse(verify_password(BOUNDARY_73, self.hash_72))
        self.assertFalse(verify_password(BOUNDARY_72, self.hash_73))

    def test_empty_hash_is_rejected(self):
        self.assertFalse(verify_password("anything", ""))

    def test_dirty_hash_is_rejected_without_raising(self):
        """库里存了非 bcrypt 格式的脏数据时当作校验失败，不要把 500 抛给前端。"""
        self.assertFalse(verify_password("anything", "not-a-bcrypt-hash"))
        self.assertFalse(verify_password("anything", "$2b$12$tooshort"))


class TokenTest(unittest.TestCase):
    def test_roundtrip(self):
        token = create_access_token("user-1", "alice")
        payload = decode_access_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], "user-1")
        self.assertEqual(payload["username"], "alice")

    def test_tampered_token_is_rejected(self):
        token = create_access_token("user-1", "alice")
        # 改签名段的**中间**字符，payload 不动。
        #
        # 不能改最后一个字符：HS256 签名是 32 字节，base64url 编码后 43 个字符，
        # 43×6 = 258 位，而有效位只有 256 —— 最后一个字符的低 2 位是填充位，
        # 解码时被丢弃。所以"换掉末位字符"有概率解出完全相同的签名字节，
        # 令牌仍然有效，这条断言就会偶发失败（实测四次跑挂一次）。
        head, _, signature = token.rpartition(".")
        middle = len(signature) // 2
        flipped = "A" if signature[middle] != "A" else "B"
        tampered = f"{head}.{signature[:middle]}{flipped}{signature[middle + 1:]}"
        self.assertIsNone(decode_access_token(tampered))

    def test_wrong_secret_is_rejected(self):
        forged = jwt.encode({"sub": "user-1", "username": "alice"},
                            "x" * 40, algorithm="HS256")
        self.assertIsNone(decode_access_token(forged))

    def test_expired_token_is_rejected(self):
        from datetime import datetime, timedelta, timezone

        expired = jwt.encode(
            {"sub": "user-1", "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
            _JWT_SECRET, algorithm=settings.jwt_algorithm,
        )
        self.assertIsNone(decode_access_token(expired))

    def test_alg_none_is_rejected(self):
        """算法混淆攻击：alg=none 的令牌没有签名，绝不能被接受。"""
        unsigned = jwt.encode({"sub": "user-1"}, key="", algorithm="none")
        self.assertIsNone(decode_access_token(unsigned))

    def test_garbage_token_is_rejected(self):
        self.assertIsNone(decode_access_token("not.a.jwt"))
        self.assertIsNone(decode_access_token(""))


class CurrentUserTest(unittest.IsolatedAsyncioTestCase):
    """get_current_user 的四种失败分支 + 一种成功分支。"""

    def setUp(self):
        # get_current_user 第一行就 `from app.services.database import db`，
        # 连"凭证为空"这种不需要查库的分支也会先把库连上。测试里统一换成替身，
        # 避免动到 backend/app/data/checkpoints/sessions.db。
        # 需要特定用户的用例在内层再 patch 一次覆盖。
        self._db_patch = mock.patch.dict(
            sys.modules, {"app.services.database": _fake_database({})}
        )
        self._db_patch.start()
        self.addCleanup(self._db_patch.stop)

    async def test_missing_credentials(self):
        with self.assertRaises(HTTPException) as ctx:
            await get_current_user(None)
        self._assert_unauthorized(ctx.exception)

    async def test_empty_credentials(self):
        with self.assertRaises(HTTPException) as ctx:
            await get_current_user(_bearer(""))
        self._assert_unauthorized(ctx.exception)

    async def test_invalid_token(self):
        with self.assertRaises(HTTPException) as ctx:
            await get_current_user(_bearer("garbage"))
        self._assert_unauthorized(ctx.exception)

    async def test_user_deleted_after_token_issued(self):
        """令牌还没过期，但用户已被删除：必须 401，不能放行一个不存在的用户。"""
        token = create_access_token("gone", "ghost")
        with mock.patch.dict(sys.modules, {"app.services.database": _fake_database({})}):
            with self.assertRaises(HTTPException) as ctx:
                await get_current_user(_bearer(token))
        self._assert_unauthorized(ctx.exception)

    async def test_valid_token_returns_user(self):
        user = {"id": "u1", "username": "alice"}
        token = create_access_token("u1", "alice")
        with mock.patch.dict(sys.modules, {"app.services.database": _fake_database({"u1": user})}):
            got = await get_current_user(_bearer(token))
        self.assertEqual(got, user)

    def _assert_unauthorized(self, exc):
        self.assertEqual(exc.status_code, 401)
        self.assertEqual(exc.detail, "未登录或登录已过期")
        self.assertEqual(exc.headers.get("WWW-Authenticate"), "Bearer")


if __name__ == "__main__":
    unittest.main(verbosity=2)
