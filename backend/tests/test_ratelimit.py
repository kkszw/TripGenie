# author: szw
"""请求级限流：令牌桶、并发槽位、以及它们和 SSE 生命周期的绑定。

时钟全部注入，不 sleep：令牌补充是时间函数，用假时钟能把"1 分钟后补回 1 个"
这类断言写成确定值。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from app.api import chat as chat_api  # noqa: E402
from app.core.ratelimit import RateLimiter  # noqa: E402
from app.models.chat import ChatRequest  # noqa: E402


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestTokenBucket(unittest.TestCase):
    """速率维度。burst=3 / 60 每分钟 = 每秒 1 个。"""

    def setUp(self):
        self.clock = FakeClock()
        self.limiter = RateLimiter(per_minute=60, burst=3, clock=self.clock)

    def test_burst_then_blocked(self):
        results = [self.limiter.acquire("u1") for _ in range(3)]
        self.assertTrue(all(ok for ok, _, _ in results))

        ok, retry_after, reason = self.limiter.acquire("u1")
        self.assertFalse(ok)
        self.assertEqual(reason, "rate")
        # 差 1 个令牌，速率 1 个/秒
        self.assertAlmostEqual(retry_after, 1.0, places=3)

    def test_refill_over_time(self):
        for _ in range(3):
            self.limiter.acquire("u1")
        self.assertFalse(self.limiter.acquire("u1")[0])

        self.clock.advance(0.5)
        # 只补了半个，仍然不够
        ok, retry_after, _ = self.limiter.acquire("u1")
        self.assertFalse(ok)
        self.assertAlmostEqual(retry_after, 0.5, places=3)

        self.clock.advance(0.5)
        self.assertTrue(self.limiter.acquire("u1")[0])

    def test_bucket_never_exceeds_capacity(self):
        # 闲置一小时也只补到容量，不会攒出一次超大突发
        self.clock.advance(3600)
        allowed = sum(1 for _ in range(10) if self.limiter.acquire("u1")[0])
        self.assertEqual(allowed, 3)

    def test_keys_are_isolated(self):
        for _ in range(3):
            self.limiter.acquire("u1")
        self.assertFalse(self.limiter.acquire("u1")[0])
        self.assertTrue(self.limiter.acquire("u2")[0])


class TestConcurrency(unittest.TestCase):
    """并发维度：只限同时挂着的流，不算速率。"""

    def setUp(self):
        self.clock = FakeClock()
        self.limiter = RateLimiter(max_concurrent=2, clock=self.clock)

    def test_blocks_third_stream(self):
        self.assertTrue(self.limiter.acquire("u1")[0])
        self.assertTrue(self.limiter.acquire("u1")[0])
        ok, _, reason = self.limiter.acquire("u1")
        self.assertFalse(ok)
        self.assertEqual(reason, "concurrent")

    def test_release_frees_slot(self):
        self.limiter.acquire("u1")
        self.limiter.acquire("u1")
        self.assertFalse(self.limiter.acquire("u1")[0])

        self.limiter.release("u1")
        self.assertTrue(self.limiter.acquire("u1")[0])

    def test_release_is_idempotent(self):
        self.limiter.release("u1")  # 没占过
        self.limiter.acquire("u1")
        self.limiter.release("u1")
        self.limiter.release("u1")  # 重复归还不能变成负数
        self.assertEqual(self.limiter.state()["inflight"].get("u1"), None)
        self.assertTrue(self.limiter.acquire("u1")[0])

    def test_concurrency_check_precedes_rate(self):
        # 并发占满时，即使令牌充足也报 concurrent，让提示更准确
        limiter = RateLimiter(per_minute=60, burst=3, max_concurrent=1,
                             clock=FakeClock())
        limiter.acquire("u1")
        _, _, reason = limiter.acquire("u1")
        self.assertEqual(reason, "concurrent")


class TestDisabled(unittest.TestCase):
    def test_all_zero_means_unlimited(self):
        limiter = RateLimiter(per_minute=0, burst=0, max_concurrent=0,
                              clock=FakeClock())
        self.assertFalse(limiter.enabled)
        for _ in range(200):
            self.assertTrue(limiter.acquire("u1")[0])
        limiter.release("u1")  # 不跟踪也不该报错

    def test_rate_only_ignores_concurrency(self):
        # 没设并发上限时，同时占很多个也不互相阻塞（只受令牌数限制）
        limiter = RateLimiter(per_minute=60, burst=10, clock=FakeClock())
        self.assertTrue(all(limiter.acquire("u1")[0] for _ in range(10)))


class TestPrune(unittest.TestCase):
    def test_idle_buckets_are_dropped(self):
        clock = FakeClock()
        limiter = RateLimiter(per_minute=60, burst=1, clock=clock)
        for i in range(5):
            limiter.acquire(f"u{i}")
            limiter.release(f"u{i}")  # 模拟请求正常结束
        self.assertEqual(limiter.state()["keys"], 5)

        limiter._prune(clock())  # 还没闲置，不该清
        self.assertEqual(limiter.state()["keys"], 5)

        clock.advance(601)
        limiter._prune(clock())
        self.assertEqual(limiter.state()["keys"], 0)

    def test_inflight_bucket_is_kept(self):
        clock = FakeClock()
        limiter = RateLimiter(per_minute=60, burst=1, max_concurrent=1, clock=clock)
        limiter.acquire("u1")
        clock.advance(601)
        limiter._prune(clock())
        # 还在跑的请求不能因为"闲置"被清掉，否则并发计数就丢了
        self.assertEqual(limiter.state()["keys"], 1)
        self.assertEqual(limiter.state("u1")["inflight"], 1)


class TestAcquireSlot(unittest.TestCase):
    """接口层：超限必须是 429 + Retry-After，而不是 500 或空流。"""

    def test_429_without_retry_after_when_concurrent(self):
        limiter = RateLimiter(max_concurrent=1, clock=FakeClock())
        with mock.patch.object(chat_api, "rate_limiter", limiter):
            chat_api._acquire_slot("u1")
            with self.assertRaises(HTTPException) as ctx:
                chat_api._acquire_slot("u1")

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("还在生成", ctx.exception.detail)
        # 并发拒绝不给时间承诺：1 秒后重试只会再撞一次
        self.assertIsNone(ctx.exception.headers)

    def test_rate_limit_message_has_seconds(self):
        clock = FakeClock()
        limiter = RateLimiter(per_minute=60, burst=1, clock=clock)
        with mock.patch.object(chat_api, "rate_limiter", limiter):
            chat_api._acquire_slot("u1")
            with self.assertRaises(HTTPException) as ctx:
                chat_api._acquire_slot("u1")

        self.assertEqual(ctx.exception.headers["Retry-After"], "1")
        self.assertIn("频繁", ctx.exception.detail)

    def test_allows_when_disabled(self):
        limiter = RateLimiter(clock=FakeClock())
        with mock.patch.object(chat_api, "rate_limiter", limiter):
            for _ in range(20):
                chat_api._acquire_slot("u1")  # 不抛就算过


class TestEndpointWiring(unittest.IsolatedAsyncioTestCase):
    """接口里限流的位置：必须在建流之前拒掉，而不是流开了才发现。"""

    async def test_second_request_blocked_before_stream(self):
        limiter = RateLimiter(max_concurrent=1, clock=FakeClock())
        req = ChatRequest(message="你好", thread_id="t1")
        user = {"id": "u1", "username": "tester"}

        with mock.patch.object(chat_api, "rate_limiter", limiter), \
                mock.patch.object(chat_api.db, "get_session",
                                  return_value={"user_id": "u1", "name": "旧会话"}):
            resp = await chat_api.send_chat(req, user)
            # 第一条：流还没开始消费，但槽位已经占住
            self.assertIsNotNone(getattr(resp, "body_iterator", None))
            self.assertEqual(limiter.state("u1")["inflight"], 1)

            with self.assertRaises(HTTPException) as ctx:
                await chat_api.send_chat(req, user)

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("还在生成", ctx.exception.detail)

    async def test_limited_request_does_not_create_session(self):
        """被拒的请求不该在会话列表里留一条空会话。"""
        limiter = RateLimiter(max_concurrent=1, clock=FakeClock())
        limiter.acquire("u1")
        req = ChatRequest(message="你好")  # 不带 thread_id，走"新建会话"那条路
        user = {"id": "u1", "username": "tester"}

        with mock.patch.object(chat_api, "rate_limiter", limiter), \
                mock.patch.object(chat_api.db, "get_session", return_value=None), \
                mock.patch.object(chat_api.db, "create_session") as created:
            with self.assertRaises(HTTPException):
                await chat_api.send_chat(req, user)

        created.assert_not_called()


class TestWithSlot(unittest.IsolatedAsyncioTestCase):
    """槽位归还必须绑在流的生命周期上，三条出口都不能漏。"""

    def setUp(self):
        self.limiter = RateLimiter(max_concurrent=1, clock=FakeClock())
        self.patcher = mock.patch.object(chat_api, "rate_limiter", self.limiter)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def test_release_after_normal_end(self):
        async def frames():
            yield "a"
            yield "b"

        out = [f async for f in chat_api._with_slot(frames(), "u1")]
        self.assertEqual(out, ["a", "b"])
        self.assertEqual(self.limiter.state("u1")["inflight"], 0)

    async def test_release_after_exception(self):
        async def frames():
            yield "a"
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            async for _ in chat_api._with_slot(frames(), "u1"):
                pass
        self.assertEqual(self.limiter.state("u1")["inflight"], 0)

    async def test_release_after_client_disconnect(self):
        """客户端断开 = 生成器被关闭，finally 仍要跑。"""
        async def frames():
            yield "a"
            yield "b"

        stream = chat_api._with_slot(frames(), "u1")
        self.assertEqual(await stream.__anext__(), "a")
        await stream.aclose()
        self.assertEqual(self.limiter.state("u1")["inflight"], 0)

        # 断开之后并发名额应该立刻可用
        self.assertTrue(self.limiter.acquire("u1")[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
