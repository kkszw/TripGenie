# author: szw
"""行程排程器（scheduler.py）的单元测试。

这个模块是纯函数，不联网、不读库——坐标通过 coord_of 注入，所以测试能精确
构造"明明两个景点隔着 30 公里，LLM 却给了 0 分钟"这类场景。

锁三件事：
  1. 修完之后不允许再出现短间隔（相邻活动至少留 MIN_TRANSFER_MIN）；
  2. 同日景点按地理相邻排序，不能东一头西一头；
  3. 塞不下的活动要明确移出（dropped），不能写一个到不了的时间。

跑法：
    cd backend && python -m unittest tests.test_scheduler -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.scheduler import (  # noqa: E402
    DAY_END,
    DAY_START,
    MIN_TRANSFER_MIN,
    classify,
    clock,
    estimate_travel_minutes,
    meal_ceiling,
    meal_floor,
    optimize_days,
    order_points,
    parse_span,
    place_name,
    schedule_day,
    schedule_stats,
)

# 青岛市南区一片 + 崂山（40 公里外），坐标取自高德
QINGDAO = {
    "栈桥": (120.3194, 36.0617),
    "天主教堂": (120.3245, 36.0674),
    "八大关风景区": (120.3489, 36.0521),
    "五四广场": (120.3842, 36.0625),
    "台东步行街": (120.3520, 36.0860),
    "崂山巨峰游览区": (120.6320, 36.1570),
}


def coord_of(name):
    return QINGDAO.get(name)


def activity(time, name, cost=0):
    return {"time": time, "activity": name, "description": "", "cost": cost}


class ParseTest(unittest.TestCase):
    def test_span(self):
        self.assertEqual(parse_span("09:00-10:30"), (540, 630))
        self.assertEqual(parse_span("9:00~10:30"), (540, 630))
        self.assertEqual(parse_span("09:00 至 10:30"), (540, 630))

    def test_span_rejects_bad_input(self):
        for value in ("", None, "上午", "09:00", "10:30-09:00"):
            with self.subTest(value=value):
                self.assertIsNone(parse_span(value))

    def test_clock(self):
        self.assertEqual(clock(540), "09:00")
        self.assertEqual(clock(630), "10:30")
        self.assertEqual(clock(0), "00:00")


class ClassifyTest(unittest.TestCase):
    def test_meal(self):
        for text in ("午餐：本地小吃", "晚餐：海鲜排档", "早餐后出发"):
            with self.subTest(text=text):
                self.assertEqual(classify(text), "meal")

    def test_logistics(self):
        for text in ("抵达大连，入住酒店", "返程去机场", "办理退房"):
            with self.subTest(text=text):
                self.assertEqual(classify(text), "logistics")

    def test_spot(self):
        self.assertEqual(classify("游览栈桥"), "spot")
        self.assertEqual(classify("台东步行街晚餐"), "spot")


class PlaceNameTest(unittest.TestCase):
    def test_strips_leading_verb_and_tail(self):
        self.assertEqual(place_name("游览中山广场，欣赏俄式建筑群"), "中山广场")
        self.assertEqual(place_name("星海广场散步，欣赏跨海大桥"), "星海广场")
        self.assertEqual(place_name("乘车前往泰山风景区"), "泰山风景区")

    def test_keeps_name_that_looks_like_verb(self):
        """「漫步八大关」剥掉动词后剩景点名；「日落」这种没动词的不能丢。"""
        self.assertEqual(place_name("漫步八大关风景区"), "八大关风景区")
        self.assertEqual(place_name("日落"), "日落")

    def test_rejects_unusable(self):
        for value in ("", None, "游览", "这是一个非常非常长的活动描述超过二十个字符限制所以不要"):
            with self.subTest(value=value):
                self.assertIsNone(place_name(value))


class TravelEstimateTest(unittest.TestCase):
    def test_missing_coord_is_zero(self):
        self.assertEqual(estimate_travel_minutes(None, QINGDAO["栈桥"]), 0.0)

    def test_short_hop_has_overhead(self):
        """700 米的两个景点也要算 10 分钟以上：找停车场、走路、检票都要时间。"""
        self.assertGreater(estimate_travel_minutes(QINGDAO["栈桥"], QINGDAO["天主教堂"]), 10)

    def test_long_hop_is_slower(self):
        near = estimate_travel_minutes(QINGDAO["栈桥"], QINGDAO["天主教堂"])
        far = estimate_travel_minutes(QINGDAO["栈桥"], QINGDAO["崂山巨峰游览区"])
        self.assertGreater(far, near * 3)


class OrderPointsTest(unittest.TestCase):
    def test_short_circuit(self):
        self.assertEqual(order_points([(0, 0)]), [0])
        self.assertEqual(order_points([(0, 0), (1, 1)]), [0, 1])

    def test_reduces_distance(self):
        """四个点在一条东西向直线上，乱序输入应被排成单调顺序。"""
        coords = [(120.30, 36.06), (120.60, 36.06), (120.35, 36.06), (120.45, 36.06)]
        order = order_points(coords)
        positions = [coords[i][0] for i in order]
        monotonic = positions == sorted(positions) or positions == sorted(positions, reverse=True)
        self.assertTrue(monotonic, f"顺序不是单调：{positions}")


def gaps(activities):
    """相邻活动之间的间隔（分钟）。"""
    result = []
    for current, following in zip(activities, activities[1:]):
        left = parse_span(current.get("time"))
        right = parse_span(following.get("time"))
        if left and right:
            result.append(right[0] - left[1])
    return result


class ScheduleDayTest(unittest.TestCase):
    def test_no_short_gap_after_scheduling(self):
        """核心断言：修完之后不允许再出现"上一个刚结束、下一个已经开始"。"""
        activities = [
            activity("09:00-11:00", "游览崂山巨峰游览区", 120),
            activity("11:00-12:00", "午餐：崂山农家宴", 60),
            activity("12:00-13:30", "游览栈桥"),
            activity("13:30-15:00", "游览天主教堂", 10),
            activity("15:00-17:00", "漫步八大关风景区"),
            activity("17:00-18:00", "晚餐：台东步行街", 80),
            activity("18:00-19:00", "五四广场散步"),
        ]
        scheduled, dropped = schedule_day(activities, coord_of)

        for gap in gaps(scheduled):
            with self.subTest(gap=gap):
                self.assertGreaterEqual(gap, MIN_TRANSFER_MIN - 1, "相邻活动之间没留够通勤时间")

        self.assertTrue(dropped, "7 个活动（含 40 公里外的崂山）不该全部塞得下")

    def test_same_day_spots_are_ordered_geographically(self):
        """同一天的景点要排成一条线（由西向东或由东向西都行），不能东西折返。"""
        activities = [
            activity("09:00-10:00", "游览五四广场"),
            activity("10:00-11:30", "游览栈桥"),
            activity("11:30-13:00", "漫步八大关风景区"),
        ]
        scheduled, dropped = schedule_day(activities, coord_of)
        names = [a["activity"] for a in scheduled]
        self.assertEqual(dropped, [])
        longitude = [QINGDAO[place_name(n)] for n in names]
        longitude = [c[0] for c in longitude]
        self.assertTrue(
            longitude == sorted(longitude) or longitude == sorted(longitude, reverse=True),
            f"景点顺序不是单向的：{names}",
        )

    def test_all_unknown_coords_leave_day_untouched(self):
        """一个坐标都没有时（地理编码全失败），整天原样返回，不瞎排。"""
        activities = [
            activity("09:00-10:00", "游览某不知名景点"),
            activity("10:00-11:00", "游览另一个不知名景点"),
        ]
        scheduled, dropped = schedule_day(activities, lambda _name: None)
        self.assertEqual(dropped, [])
        self.assertEqual([a["time"] for a in scheduled], ["09:00-10:00", "10:00-11:00"])

    def test_dropped_when_day_is_full(self):
        activities = [activity(f"{9 + i}:00-{10 + i}:00", "游览栈桥") for i in range(12)]
        original_times = [a["time"] for a in activities]
        scheduled, dropped = schedule_day(activities, coord_of)
        self.assertLess(len(scheduled), 12)
        self.assertEqual(len(scheduled) + len(dropped), 12)
        # 被移出的活动保留原时间（不改写成到不了的时间），交给调用方去提示用户
        for item in dropped:
            with self.subTest(item=item["time"]):
                self.assertIn(item["time"], original_times)

    def test_mixed_day_keeps_unknown_spot_in_plan(self):
        """有坐标的景点参与重排，没坐标的也不能从行程里消失。"""
        activities = [
            activity("09:00-10:00", "游览某不知名景点"),
            activity("10:00-11:00", "游览栈桥"),
        ]
        scheduled, dropped = schedule_day(activities, coord_of)
        names = [a["activity"] for a in scheduled]
        self.assertEqual(dropped, [])
        self.assertIn("游览某不知名景点", names)
        self.assertIn("游览栈桥", names)

    def test_meal_keeps_its_window(self):
        """午饭不早于 11:30 开桌，也不会被拖到 14:00 之后。"""
        activities = [
            activity("09:00-11:00", "游览栈桥"),
            activity("11:00-12:00", "午餐：本地小吃"),
            activity("13:00-14:00", "游览五四广场"),
        ]
        scheduled, _ = schedule_day(activities, coord_of)
        lunch = next(a for a in scheduled if a["activity"].startswith("午餐"))
        start = parse_span(lunch["time"])[0]
        self.assertGreaterEqual(start, 11 * 60 + 30)
        self.assertLessEqual(start, meal_ceiling("午餐", DAY_END))

    def test_does_not_mutate_input(self):
        activities = [activity("09:00-10:00", "游览五四广场"), activity("10:00-11:00", "游览栈桥")]
        original = [a["time"] for a in activities]
        schedule_day(activities, coord_of)
        self.assertEqual([a["time"] for a in activities], original)

    def test_empty_day(self):
        scheduled, dropped = schedule_day([], coord_of)
        self.assertEqual((scheduled, dropped), ([], []))


class OptimizeDaysTest(unittest.TestCase):
    def test_dropped_is_rebalanced_to_other_day(self):
        """第 1 天塞不下的活动应尽量挪到还空着的第 2 天，而不是直接丢掉。

        现实场景：县城周边景点少但彼此几十公里，LLM 会把它们塞进同一天，
        硬排必然丢掉一两个，而另一天往往空着。
        """
        days = [
            {"day": 1, "activities": [activity(f"{9 + i}:00-{10 + i}:00", "游览栈桥") for i in range(12)]},
            {"day": 2, "activities": [activity("09:00-10:00", "游览五四广场")]},
        ]
        result = optimize_days(days, coord_of)

        self.assertEqual(len(result.days), 2)
        total = sum(len(day["activities"]) for day in result.days) + len(result.dropped)
        self.assertEqual(total, 13, "活动不能凭空消失")
        self.assertGreater(len(result.days[1]["activities"]), 1,
                           "第 2 天应该接收第 1 天放不下的活动")

    def test_single_day_overfull_still_drops(self):
        """只有一天、又没有别处可挪时，排不下的还是要明确移出。"""
        days = [{"day": 1, "activities": [
            activity(f"{9 + i}:00-{10 + i}:00", "游览栈桥") for i in range(12)
        ]}]
        result = optimize_days(days, coord_of)

        self.assertTrue(result.dropped)
        for item in result.dropped:
            self.assertEqual(item["day"], 1)

    def test_stats_reports_zero_short_gap(self):
        days = [{"day": 1, "activities": [
            activity("09:00-10:00", "游览五四广场"),
            activity("10:00-11:00", "游览栈桥"),
        ]}]
        result = optimize_days(days, coord_of)
        stats = result.stats
        self.assertEqual(stats["short_gap_pairs"], 0)
        self.assertEqual(stats["short_gap_ratio"], 0.0)
        self.assertGreater(stats["travel_minutes"], 0)

    def test_stats_detects_short_gap_before_scheduling(self):
        """对照组：没排程之前，这两个活动是首尾相接的（间隔 0）。"""
        days = [{"day": 1, "activities": [
            activity("09:00-10:00", "游览五四广场"),
            activity("10:00-11:00", "游览栈桥"),
        ]}]
        stats = schedule_stats(days, coord_of)
        self.assertEqual(stats["short_gap_pairs"], 1)
        self.assertEqual(stats["time_pairs"], 1)


class MealWindowTest(unittest.TestCase):
    def test_floor(self):
        self.assertEqual(meal_floor("午餐：小吃", DAY_START), 11 * 60 + 30)
        self.assertEqual(meal_floor("晚餐：海鲜", DAY_START), 17 * 60 + 30)
        self.assertEqual(meal_floor("早餐：酒店", DAY_START), DAY_START)

    def test_ceiling(self):
        self.assertEqual(meal_ceiling("午餐：小吃", DAY_END), 13 * 60 + 30)
        self.assertEqual(meal_ceiling("晚餐：海鲜", DAY_END), 19 * 60)
        self.assertEqual(meal_ceiling("办理入住", DAY_END), DAY_END)


class OptimizeScheduleNodeTest(unittest.IsolatedAsyncioTestCase):
    """节点层：只验证它有没有把排程结果写回 state，排程算法本身由上面覆盖。"""

    def _state(self):
        return {
            "thread_id": "t",
            "destination": "山东省青岛市",
            "draft_plan": [{"day": 1, "activities": [
                activity("09:00-10:00", "游览五四广场"),
                activity("10:00-11:00", "游览栈桥"),
            ]}],
            "plan_tips": ["带外套"],
        }

    async def test_writes_back_sorted_plan(self):
        from app.core.nodes import schedule as node

        async def fake_coords(names, region, concurrency=None):
            return dict(QINGDAO)

        state = self._state()
        with mock.patch.object(node, "resolve_coords", fake_coords):
            out = await node.optimize_schedule_node(state, None)

        times = [a["time"] for a in out["draft_plan"][0]["activities"]]
        spans = [parse_span(t) for t in times]
        self.assertGreaterEqual(spans[0][0], DAY_START)
        self.assertGreaterEqual(spans[1][0] - spans[0][1], MIN_TRANSFER_MIN - 1)
        self.assertEqual(out["current_step"], "optimize_schedule")
        self.assertIn("schedule_stats", out)
        self.assertTrue(out["response"])

    async def test_missing_coords_keeps_plan_untouched(self):
        from app.core.nodes import schedule as node

        async def no_coords(names, region, concurrency=None):
            return {}

        state = self._state()
        before = [a["time"] for a in state["draft_plan"][0]["activities"]]
        with mock.patch.object(node, "resolve_coords", no_coords):
            out = await node.optimize_schedule_node(state, None)

        self.assertEqual([a["time"] for a in out["draft_plan"][0]["activities"]], before)

    async def test_resolver_failure_does_not_break_plan(self):
        """地理编码挂了是常态（限流/超时），排程必须自己让开，不能把行程带走。"""
        from app.core.nodes import schedule as node

        async def boom(names, region, concurrency=None):
            raise RuntimeError("amap down")

        state = self._state()
        before = [a["time"] for a in state["draft_plan"][0]["activities"]]
        with mock.patch.object(node, "resolve_coords", boom):
            out = await node.optimize_schedule_node(state, None)
        self.assertEqual([a["time"] for a in out["draft_plan"][0]["activities"]], before)
        self.assertNotIn("error", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
