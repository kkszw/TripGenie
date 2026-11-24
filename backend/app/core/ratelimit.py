# author: szw
"""请求级限流：按用户的令牌桶 + 并发流上限。

要解决的具体问题：`/stream` 每被调用一次就是一次完整的 LLM 运行——输入几千
token、20 个工具可选、输出几百 token，背后还挂着高德和 DeepSeek 两份额度。
没有限流时，一个 for 循环就能把两份配额一起刷光，而服务端不会有任何异常，
只是账单变大、代价是别人用不了。

两个维度分开管，因为它们防的不是同一件事：

- **速率（令牌桶）** 防"短时间内打很多次"。桶容量是允许的突发，补充速率是
  稳态速率。用令牌桶而不是固定窗口，是因为固定窗口在窗口边界会放过两倍流量
  （第 59 秒 10 个 + 第 61 秒 10 个 = 2 秒内 20 个），而令牌桶的瞬时上限
  永远等于桶容量。
- **并发** 防"同时挂很多条流"。一次规划要跑几十秒到几分钟，光有速率限制时，
  连发 3 条就会 3 条同时在图上跑，互相抢高德配额，还可能同时写同一个
  thread_id 的 checkpoint。

为什么放进程内存、不引 Redis：本项目单进程（uvicorn `--workers 1`），
进程内计数器就是全局计数器，为此多一个中间件依赖不划算。
⚠️ 但这也意味着一旦改成多 worker 或多副本，每个进程各有一份预算，
实际限额会乘以进程数或副本数，那时必须换成 Redis 之类的共享计数器。

超限时的行为：抛 429 + `Retry-After`，而不是排队等待。排队会让用户对着
转圈干等且不知道要等多久，429 至少把"多久之后可以再试"明确告诉前端。
"""
import time

from typing import Callable, Dict, Optional, Tuple

# 每这么多次请求清一次闲置用户。纯内存字典会随用户数线性增长，
# 不清的话跑几个月就是一个只进不出的字典。
_PRUNE_EVERY = 256

# 闲置超过这个秒数的桶直接丢掉。丢掉的桶下次用会重新装满，
# 但闲置这么久之后本来就该补满了，等价于保留。
_IDLE_TTL = 600.0


class TokenBucket:
    """令牌桶：容量固定，按 rate 匀速补充。"""

    __slots__ = ("capacity", "rate", "tokens", "last")

    def __init__(self, capacity: float, rate: float, now: float):
        self.capacity = capacity
        self.rate = rate
        self.tokens = capacity  # 新用户从满桶开始，允许一次突发
        self.last = now

    def take(self, now: float) -> Tuple[bool, float]:
        """消耗一个令牌。

        Returns:
            (是否通过, 距离下次有令牌的秒数；通过时为 0)
        """
        elapsed = now - self.last
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True, 0.0
        if self.rate <= 0:
            # 不补充的桶用完之后就永远空着。这里不该出现（rate=0 时不会建桶），
            # 留个兜底是为了不把一个配置错误变成 ZeroDivisionError。
            return False, float("inf")
        # 补齐到 1 个还差多少：rate 是每秒补充量
        return False, (1.0 - self.tokens) / self.rate


class RateLimiter:
    """按 key（这里是 user_id）限流。

    三个参数任一为 0 表示该维度关闭：
    - per_minute：每分钟补充的令牌数（稳态速率）
    - burst：桶容量（允许的瞬时突发）
    - max_concurrent：同一 key 同时在跑的请求数上限

    时钟可注入，测试里不用 sleep 就能验证补充逻辑。
    """

    def __init__(self, per_minute: int = 0, burst: int = 0,
                 max_concurrent: int = 0,
                 clock: Callable[[], float] = time.monotonic):
        self.per_minute = max(0, per_minute)
        self.rate = self.per_minute / 60.0
        self.capacity = float(burst if burst > 0 else max(self.per_minute, 1))
        self.max_concurrent = max(0, max_concurrent)
        self._clock = clock
        self._buckets: Dict[str, TokenBucket] = {}
        self._inflight: Dict[str, int] = {}
        self._acquires = 0

    @property
    def enabled(self) -> bool:
        return self.per_minute > 0 or self.max_concurrent > 0

    def acquire(self, key: str) -> Tuple[bool, float, str]:
        """占一个请求槽位。

        Returns:
            (是否通过, 建议等待秒数, 拒绝原因："" / "rate" / "concurrent")
        """
        if not self.enabled:
            return True, 0.0, ""

        now = self._clock()
        self._acquires += 1
        if self._acquires % _PRUNE_EVERY == 0:
            self._prune(now)

        # 先看并发再看速率：并发是被"正在跑的长请求"占住的，重试也不一定马上
        # 能拿到，先报并发能让前端提示得更准确（"上一条还在生成" vs "太频繁"）
        if self.max_concurrent > 0 and self._inflight.get(key, 0) >= self.max_concurrent:
            return False, 1.0, "concurrent"

        if self.rate > 0:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(self.capacity, self.rate, now)
                self._buckets[key] = bucket
            ok, retry_after = bucket.take(now)
            if not ok:
                return False, retry_after, "rate"

        self._inflight[key] = self._inflight.get(key, 0) + 1
        return True, 0.0, ""

    def release(self, key: str) -> None:
        """归还并发槽位。必须在 finally 里调用，否则该用户会被自己永久占住。"""
        current = self._inflight.get(key)
        if not current:
            return
        if current <= 1:
            self._inflight.pop(key, None)
        else:
            self._inflight[key] = current - 1

    # ==================== 内部 ====================

    def _prune(self, now: float) -> None:
        for key, bucket in list(self._buckets.items()):
            if key in self._inflight:
                continue
            if now - bucket.last >= _IDLE_TTL:
                del self._buckets[key]

    # ==================== 观测 ====================

    def state(self, key: Optional[str] = None) -> Dict:
        """当前状态，给测试和排查用（正常调用链不读它）。"""
        now = self._clock()
        if key is not None:
            bucket = self._buckets.get(key)
            return {
                "tokens": round(bucket.tokens, 2) if bucket else None,
                "inflight": self._inflight.get(key, 0),
                "now": now,
            }
        return {
            "keys": len(self._buckets),
            "inflight": dict(self._inflight),
        }
