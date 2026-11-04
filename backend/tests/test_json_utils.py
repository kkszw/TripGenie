# author: szw
"""模型输出容错解析的单元测试（纯函数，不联网、不起服务）。

为什么值得单测：整条行程生成都挂在 parse_llm_json 上。DeepSeek 实测会把 JSON
包进 ```json 围栏（几乎必现），一旦这里解析失败，generate_plan 节点走 except
分支，前端只看到一段原始文本、既没有行程卡片也没有地图。这个文件只有 48 行，
改一个正则就能让全站退化，改坏了却没有任何提示——所以正例反例都固化下来。

跑法：
    cd backend && python -m unittest tests.test_json_utils -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.json_utils import as_number, parse_llm_json  # noqa: E402


class ParseLlmJsonTest(unittest.TestCase):
    """parse_llm_json：容错要够宽，但绝不能把非 JSON 硬解析成 JSON。"""

    def test_plain_json(self):
        self.assertEqual(parse_llm_json('{"days": [{"day": 1}]}'), {"days": [{"day": 1}]})

    def test_json_code_fence(self):
        """DeepSeek 的默认行为：把 JSON 包进 ```json 围栏。"""
        text = '```json\n{"days": [{"day": 1}], "tips": ["带外套"]}\n```'
        self.assertEqual(parse_llm_json(text)["tips"], ["带外套"])

    def test_plain_code_fence_without_language(self):
        """围栏不一定带语言标记，模型有时只写 ```。"""
        self.assertEqual(parse_llm_json('```\n{"a": 1}\n```'), {"a": 1})

    def test_json_with_surrounding_prose(self):
        """模型爱在 JSON 前后加"好的，以下是行程"这类话，不能因此整条失败。"""
        text = '好的，以下是我为您规划的行程：\n{"days": [{"day": 1}], "tips": []}\n祝您旅途愉快！'
        self.assertEqual(parse_llm_json(text)["days"], [{"day": 1}])

    def test_nested_braces_take_outermost(self):
        """嵌套对象里有多个 }，取最外层才是完整的 JSON。"""
        text = '说明文字 {"days": [{"attractions": [{"name": "栈桥"}]}], "total": 1} 结尾'
        parsed = parse_llm_json(text)
        self.assertEqual(parsed["total"], 1)
        self.assertEqual(parsed["days"][0]["attractions"][0]["name"], "栈桥")

    def test_chinese_content_survives(self):
        text = json.dumps({"destination": "青岛", "tips": ["八大关适合散步"]}, ensure_ascii=False)
        self.assertEqual(parse_llm_json(text)["destination"], "青岛")

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            parse_llm_json("")

    def test_plain_text_raises(self):
        with self.assertRaises(ValueError):
            parse_llm_json("抱歉，我无法完成这个请求。")

    def test_truncated_json_raises(self):
        """流式中途断开留下的半截 JSON，必须报错而不是返回残缺 dict。"""
        with self.assertRaises(ValueError):
            parse_llm_json('{"days": [{"day": 1, "attractions": [')

    def test_non_string_input_raises_value_error(self):
        """传 None 时 str(None) 也解析不出来，要抛 ValueError 而不是 TypeError，
        否则调用方写 except ValueError 会漏掉。"""
        with self.assertRaises(ValueError):
            parse_llm_json(None)


class AsNumberTest(unittest.TestCase):
    """as_number：把模型/用户给的天数、预算宽松转成数字，转不了就当没提供。"""

    def test_int_passthrough(self):
        self.assertEqual(as_number(3), 3)

    def test_numeric_string(self):
        self.assertEqual(as_number("3"), 3)

    def test_string_with_unit(self):
        """用户真实写法："3天""1500元"。"""
        self.assertEqual(as_number("3天"), 3)
        self.assertEqual(as_number("1500元"), 1500)

    def test_float_truncates(self):
        self.assertEqual(as_number(3.7), 3)
        self.assertEqual(as_number("3.7天"), 3)

    def test_chinese_number_is_not_guessed(self):
        """「三天」不猜数字，宁可当作没给、让 clarify 去问，也不要瞎猜成 3。"""
        self.assertIsNone(as_number("三天"))

    def test_bool_is_not_number(self):
        """True 在 Python 里是 1，但语义上「是/否」不是天数。"""
        self.assertIsNone(as_number(True))
        self.assertIsNone(as_number(False))

    def test_none(self):
        self.assertIsNone(as_number(None))

    def test_garbage(self):
        self.assertIsNone(as_number("随便"))
        self.assertIsNone(as_number([]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
