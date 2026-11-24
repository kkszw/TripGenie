# author: szw
"""上下文 token 预算的离线测试：估算、截断位置、配对补齐。

纯函数测试，不碰数据库、不联网。真实成本要靠 runs.jsonl 里的实际
input_tokens 回头看，这里只保证截断逻辑本身不走样。
"""
import unittest

from app.services.database import estimate_tokens, trim_to_token_budget


def msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


class TestEstimateTokens(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens(None), 0)

    def test_chinese_one_per_char(self):
        self.assertEqual(estimate_tokens("北京三日游"), 5)

    def test_ascii_four_chars_per_token(self):
        self.assertEqual(estimate_tokens("abcdefgh"), 2)

    def test_mixed(self):
        # 2 个汉字 + 4 个 ASCII = 2 + ceil(4/4) = 3
        self.assertEqual(estimate_tokens("行程abcd"), 3)


class TestTrimToTokenBudget(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(trim_to_token_budget([], 100), [])

    def test_zero_means_unlimited(self):
        history = [msg("user", "你好"), msg("assistant", "在的")]
        self.assertEqual(trim_to_token_budget(history, 0), history)

    def test_all_within_budget(self):
        history = [msg("user", "你好"), msg("assistant", "在的")]
        self.assertEqual(trim_to_token_budget(history, 1000), history)

    def test_keeps_only_most_recent(self):
        # 每条 10 个汉字 = 10 token，预算 25 → 装得下最后两条
        history = [msg("user", "一二三四五六七八九十") for _ in range(5)]
        kept = trim_to_token_budget(history, 25)
        self.assertEqual(len(kept), 2)
        self.assertIs(kept[-1], history[-1])

    def test_always_keeps_at_least_one(self):
        # 单条就远超预算（例如整份行程 JSON）也要留一条，空历史更糟
        history = [msg("assistant", "汉" * 5000)]
        self.assertEqual(len(trim_to_token_budget(history, 100)), 1)

    def test_pairs_leading_assistant_with_its_question(self):
        # 预算只够最后一条 assistant，应把它前面的 user 一起带上，
        # 否则模型会看到一条没有对应提问的回复
        history = [
            msg("user", "第一问" + "长" * 100),
            msg("assistant", "第一答" + "长" * 100),
            msg("user", "第二问"),
            msg("assistant", "第二答"),
        ]
        kept = trim_to_token_budget(history, 5)
        self.assertEqual([m["role"] for m in kept], ["user", "assistant"])
        self.assertEqual(kept[0]["content"], "第二问")

    def test_order_is_chronological(self):
        history = [msg("user", "a" * 40), msg("assistant", "b" * 40)]
        kept = trim_to_token_budget(history, 1000)
        self.assertEqual([m["role"] for m in kept], ["user", "assistant"])

    def test_long_conversation_stays_within_budget(self):
        # 20 轮问答，每轮 user 10 token + assistant 40 token，预算 100
        history = []
        for i in range(20):
            history.append(msg("user", f"问{i}" + "字" * 8))
            history.append(msg("assistant", "答" + "字" * 39))
        kept = trim_to_token_budget(history, 100)
        total = sum(estimate_tokens(m["content"]) for m in kept)
        # 允许补一条提问带来的少量超出
        self.assertLessEqual(total, 100 + 10)
        self.assertEqual(kept[-1], history[-1])


if __name__ == "__main__":
    unittest.main()
