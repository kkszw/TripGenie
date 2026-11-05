# author: szw
"""锁住「类型定义 ↔ prompt 模板 ↔ 实际数据」三者一致。

这条一致性曾经断了很久，而且全程不报错：

  state.py 的 DayPlan       attractions / meals / hotel / transport / budget
  prompts/plan.py 的模板     activities / notes / estimatedCost / theme / weather
  实际跑出来的数据           跟模板一致，跟 DayPlan 完全不一样

结果是 format_response 按 DayPlan 去读 attractions，每天只剩一个
「📅 Day 1: 」和空行，而排程节点又会拿它覆盖 state["response"]——
每一趟跑完的正文都是坏的，日志里一句警告都没有。

类型注解在运行时不做任何校验，所以它只能靠测试来保证不漂。
"""
import json
import unittest

from app.core.nodes.plan import format_response
from app.core.prompts import PLAN_TEMPLATE
from app.core.state import Activity, DayPlan


class PlanTemplateTest(unittest.TestCase):
    """模板是给模型看的唯一结构说明，它错了模型就会照着错。"""

    def setUp(self):
        self.sample = json.loads(PLAN_TEMPLATE)

    def test_template_is_valid_json(self):
        # 这条不是废话：模板里曾经有一个尾随逗号和一处缺失的 `]`，
        # 模型很容易把这种写法当范例学过去，最后坏在解析环节。
        self.assertIn("days", self.sample)
        self.assertIsInstance(self.sample["days"], list)
        self.assertTrue(self.sample["days"])

    def test_day_keys_match_typed_dict(self):
        actual = set(self.sample["days"][0])
        expected = set(DayPlan.__annotations__)
        self.assertEqual(
            actual, expected,
            f"模板里的日字段 {sorted(actual)} 与 DayPlan {sorted(expected)} 不一致；"
            "改了一边必须同时改另一边，否则读数据的人会读到空",
        )

    def test_activity_keys_match_typed_dict(self):
        actual = set(self.sample["days"][0]["activities"][0])
        expected = set(Activity.__annotations__)
        self.assertEqual(
            actual, expected,
            f"模板里的活动字段 {sorted(actual)} 与 Activity {sorted(expected)} 不一致",
        )

    def test_top_level_keys_match_plan_node(self):
        # plan 节点从顶层取的就是这两个键，改名会让 draft_plan 变成空列表
        self.assertIn("days", self.sample)
        self.assertIn("tips", self.sample)


class FormatResponseTest(unittest.TestCase):
    """排程节点会用它覆盖 response，所以它必须真的读得出活动名。"""

    PLAN = {"days": [
        {"day": 1, "activities": [
            {"time": "09:00-10:30", "activity": "抵达三亚，入住酒店", "cost": 0},
            {"time": "11:00-12:30", "activity": "游览椰梦长廊，漫步海滨风景大道", "cost": 0},
            {"time": "12:45-13:45", "activity": "午餐：第一市场海鲜排档", "cost": 60},
        ]},
        {"day": 2, "activities": [
            {"time": "10:30-13:00", "activity": "游览亚龙湾热带天堂森林公园", "cost": 158},
        ]},
    ]}

    def test_renders_activity_names(self):
        text = format_response(self.PLAN)
        self.assertIn("椰梦长廊", text)
        self.assertIn("亚龙湾热带天堂森林公园", text)

    def test_keeps_entries_without_place_name(self):
        # 一天里不只有景点，还有午餐、返程。抽不出地名不能整条丢掉，
        # 否则摘要读起来像漏了一顿饭。
        text = format_response(self.PLAN)
        self.assertIn("午餐", text)

    def test_adjacent_same_place_is_collapsed(self):
        # 「前往X」和「游览X」是两条真活动，但摘要里连写两遍同一个名字
        # 只会让人以为排重了
        plan = {"days": [{"day": 3, "activities": [
            {"time": "09:15-10:15", "activity": "前往大小洞天景区", "cost": 0},
            {"time": "10:25-12:55", "activity": "游览大小洞天景区", "cost": 108},
        ]}]}
        text = format_response(plan)
        self.assertEqual(text.count("大小洞天"), 1)

    def test_day_with_no_activities_does_not_crash(self):
        text = format_response({"days": [{"day": 3}]})
        self.assertIn("Day 3", text)

    def test_empty_plan(self):
        self.assertIn("为您规划", format_response({}))


if __name__ == "__main__":
    unittest.main()
