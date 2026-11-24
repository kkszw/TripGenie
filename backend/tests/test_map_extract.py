# author: szw
"""景点提取的容错测试（不联网、不调模型）。

为什么单独一个文件：2026-11 出现过「地图静默变空」——行程正常生成，但景点
提取的响应是 JSON 数组，代码按对象取 `.get("attractions")` 抛 AttributeError，
被 `except Exception` 吞掉后返回空列表，前端照常渲染地图区块并显示
「这一天没有需要标注的景点，可能是自由活动或返程」。把程序错误说成了
行程本身没写地点，而且日志里只有一行 ERR，谁都没发现。

当时的 tests/test_json_utils.py 只有对象形状的用例，map_data 的提取函数零覆盖，
所以这段代码错了三个月没被任何断言拦住。这里把「模型可能返回的几种形状」
和「失败必须抛错、不能返回空」都固化下来。

跑法：
    cd backend && python -m unittest tests.test_map_extract -v
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import AIMessage  # noqa: E402

from app.core import llm as llm_runtime  # noqa: E402
from app.services.map_data import (  # noqa: E402
    AttractionExtractionError,
    extract_attractions_with_llm,
    generate_map_data,
    normalize_attractions,
)

PLAN = {
    "days": [
        {"day": 1, "activities": [{"activity": "栈桥"}, {"activity": "八大关"}]},
    ]
}


def run_extract(content, *, raises=None):
    """用假的模型响应跑一次提取。

    raises 传异常类时断言抛出——提取失败必须显式抛，不能靠返回空列表
    让调用方以为「这天没景点」。
    """
    fake = type("FakeLLM", (), {})()
    fake.ainvoke = AsyncMock(return_value=AIMessage(content=content))
    with patch.object(llm_runtime, "llm_extract", fake):
        if raises:
            with unittest.TestCase().assertRaises(raises):
                asyncio.run(extract_attractions_with_llm("青岛", PLAN))
            return None
        return asyncio.run(extract_attractions_with_llm("青岛", PLAN))


class NormalizeAttractionsTest(unittest.TestCase):
    """normalize_attractions：把模型返回的各种形状归一成景点列表。"""

    def test_object_shape(self):
        """prompt 要求的形状：{"attractions": [...]}。"""
        out = normalize_attractions({"attractions": [{"name": "栈桥", "day": 1, "city": "青岛"}]})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "栈桥")
        self.assertEqual(out[0]["city"], "青岛")

    def test_bare_list_shape(self):
        """模型按「返回 JSON 数组」理解时的形状——本次 bug 的入口。"""
        out = normalize_attractions([{"name": "栈桥", "day": 1}])
        self.assertEqual([a["name"] for a in out], ["栈桥"])

    def test_string_list_is_skipped(self):
        """纯字符串数组没有 day/city，对应不到某一天，跳过比瞎猜好。"""
        self.assertEqual(normalize_attractions(["栈桥", "八大关"]), [])

    def test_alternate_key(self):
        """键名被写成 items 时，对象里只有一个数组就用它。"""
        out = normalize_attractions({"items": [{"name": "栈桥"}]})
        self.assertEqual(out[0]["name"], "栈桥")

    def test_no_list_value_returns_empty(self):
        self.assertEqual(normalize_attractions({"foo": 1}), [])

    def test_multiple_list_values_is_ambiguous(self):
        """对象里有两个数组时无法判断哪个是景点，返回空而不是赌一个。"""
        self.assertEqual(normalize_attractions({"a": [{"name": "x"}], "b": []}), [])

    def test_non_dict_items_skipped(self):
        out = normalize_attractions([{"name": "栈桥"}, "八大关", 42, None])
        self.assertEqual([a["name"] for a in out], ["栈桥"])

    def test_missing_or_blank_name_skipped(self):
        """没有名字的景点没法地理编码，留着只会变成地图上一个空点。"""
        out = normalize_attractions([{"day": 1}, {"name": "   "}, {"name": "栈桥"}])
        self.assertEqual([a["name"] for a in out], ["栈桥"])

    def test_name_and_city_trimmed(self):
        out = normalize_attractions([{"name": " 栈桥 ", "city": " 青岛 "}])
        self.assertEqual(out[0]["name"], "栈桥")
        self.assertEqual(out[0]["city"], "青岛")

    def test_string_day_converted(self):
        out = normalize_attractions([{"name": "栈桥", "day": "2"}])
        self.assertEqual(out[0]["day"], 2)

    def test_unparsable_day_defaults_to_one(self):
        """模型偶尔写「第二天」，as_number 不猜中文数字，退回第 1 天。"""
        out = normalize_attractions([{"name": "栈桥", "day": "第二天"}])
        self.assertEqual(out[0]["day"], 1)

    def test_not_a_dict(self):
        self.assertEqual(normalize_attractions("栈桥"), [])
        self.assertEqual(normalize_attractions(None), [])


class ExtractAttractionsTest(unittest.TestCase):
    """extract_attractions_with_llm：端到端的解析路径。"""

    def test_object_response(self):
        content = '```json\n{"attractions": [{"name": "栈桥", "day": 1, "city": "青岛"}]}\n```'
        out = run_extract(content)
        self.assertEqual([a["name"] for a in out], ["栈桥"])

    def test_bare_array_response(self):
        """回归：模型直接返回数组时必须能提取，不能静默变空。"""
        content = '[{"name": "栈桥", "day": 1, "city": "青岛"}]'
        out = run_extract(content)
        self.assertEqual([a["name"] for a in out], ["栈桥"])

    def test_fenced_array_response(self):
        content = '```json\n[{"name": "栈桥"}, {"name": "八大关"}]\n```'
        self.assertEqual([a["name"] for a in run_extract(content)], ["栈桥", "八大关"])

    def test_array_with_prose(self):
        content = '提取结果如下：\n[{"name": "栈桥"}]\n希望有帮助。'
        self.assertEqual([a["name"] for a in run_extract(content)], ["栈桥"])

    def test_empty_array_is_a_normal_answer(self):
        """空数组是合法结论（这天全是餐饮/返程），不能当成失败抛错。"""
        self.assertEqual(run_extract("[]"), [])

    def test_empty_attractions_field_is_normal(self):
        self.assertEqual(run_extract('{"attractions": []}'), [])

    def test_unparsable_raises(self):
        """解析不出来必须抛错：返回 [] 会让前端说"这天可能自由活动"。"""
        run_extract("抱歉，我无法完成这个请求。", raises=AttractionExtractionError)

    def test_model_call_failure_raises(self):
        fake = type("FakeLLM", (), {})()
        fake.ainvoke = AsyncMock(side_effect=RuntimeError("connection reset"))
        with patch.object(llm_runtime, "llm_extract", fake):
            with self.assertRaises(AttractionExtractionError):
                asyncio.run(extract_attractions_with_llm("青岛", PLAN))


class MapDataFailureTest(unittest.TestCase):
    """提取失败时 generate_map_data 必须显式标记，不能返回一个"看起来正常"的空地图。"""

    def test_extract_failure_is_marked(self):
        with patch.object(llm_runtime, "ensure_tools", AsyncMock()), \
                patch("app.services.map_data.extract_attractions_with_llm",
                      AsyncMock(side_effect=AttractionExtractionError("解析失败"))):
            out = asyncio.run(generate_map_data({"destination": "青岛"}, PLAN))

        # 返回非空，才会写进 state 让前端读到标记；
        # 只返回 {} 的话前端根本不知道出过事
        self.assertTrue(out.get("extract_failed"))
        self.assertEqual(out["attractions"], [])
        self.assertEqual(out["routes"], [])
        self.assertEqual(out["destination"], "青岛")
        # center 必须是有值的两点，前端拿它当兜底中心
        self.assertEqual(len(out["center"]), 2)

    def test_normal_empty_result_is_not_marked(self):
        """这天真的没景点时不能打 extract_failed，否则用户看到的是假警报。"""
        with patch.object(llm_runtime, "ensure_tools", AsyncMock()), \
                patch("app.services.map_data.extract_attractions_with_llm",
                      AsyncMock(return_value=[])):
            out = asyncio.run(generate_map_data({"destination": "青岛"}, PLAN))

        self.assertNotIn("extract_failed", out)
        self.assertEqual(out["attractions"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
