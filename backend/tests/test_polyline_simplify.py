# author: szw
"""折线抽稀的回归测试。

只依赖标准库，任何 3.8+ 解释器都能跑：
    cd backend && python tests/test_polyline_simplify.py
    cd backend && python -m unittest tests.test_polyline_simplify -v

重点不是"能减少点数"，而是"减少后形状还对"：
城市路网全是直角，等间隔采样会把拐角抹掉，所以必须验证拐点被保留。
"""
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.polyline import max_deviation, simplify_polyline  # noqa: E402

# 大连星海广场附近，经度 1 度约 87km、纬度 1 度约 111km
BASE_LNG = 121.5888
BASE_LAT = 38.8823


def straight(count, lng_step=0.0001):
    """一条正东方向的直线，抽稀后理论上只剩首尾两点。"""
    return [[BASE_LNG + i * lng_step, BASE_LAT] for i in range(count)]


class TestBasicBehavior(unittest.TestCase):
    def test_empty_and_short_input(self):
        self.assertEqual(simplify_polyline([]), [])
        self.assertEqual(simplify_polyline(None), [])
        self.assertEqual(simplify_polyline([[BASE_LNG, BASE_LAT]]), [[BASE_LNG, BASE_LAT]])
        two = [[BASE_LNG, BASE_LAT], [BASE_LNG + 0.001, BASE_LAT]]
        self.assertEqual(simplify_polyline(two), [[round(p, 6) for p in point] for point in two])

    def test_straight_line_collapses_to_endpoints(self):
        points = straight(100)
        result = simplify_polyline(points, tolerance_m=6)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], [round(points[0][0], 6), round(points[0][1], 6)])
        self.assertEqual(result[-1], [round(points[-1][0], 6), round(points[-1][1], 6)])

    def test_right_angle_keeps_corner(self):
        """直角拐弯的点必须留下，否则线会斜穿建筑。

        拐角两段各留 100 米以上：8 米的小直角在 6 米容差下本来就可以切角
        （斜切偏移约 3 米），那是 DP 的正确行为，不该拿来当反例。
        """
        corner = [BASE_LNG + 0.002, BASE_LAT]
        points = straight(20, lng_step=0.0001) + [
            [corner[0], BASE_LAT + i * 0.0001] for i in range(20)
        ]
        result = simplify_polyline(points, tolerance_m=6)
        self.assertIn(corner, result)
        self.assertEqual(len(result), 3)

    def test_first_and_last_are_always_kept(self):
        points = [[BASE_LNG + math.sin(i / 3) * 0.001, BASE_LAT + i * 0.0001] for i in range(200)]
        result = simplify_polyline(points, tolerance_m=6)
        self.assertEqual(result[0], [round(points[0][0], 6), round(points[0][1], 6)])
        self.assertEqual(result[-1], [round(points[-1][0], 6), round(points[-1][1], 6)])

    def test_duplicate_neighbors_are_removed(self):
        points = [
            [BASE_LNG, BASE_LAT],
            [BASE_LNG, BASE_LAT],
            [BASE_LNG + 0.001, BASE_LAT],
            [BASE_LNG + 0.001, BASE_LAT],
        ]
        self.assertEqual(len(simplify_polyline(points)), 2)

    def test_all_identical_points(self):
        points = [[BASE_LNG, BASE_LAT]] * 20
        self.assertEqual(simplify_polyline(points), [[BASE_LNG, BASE_LAT]])


class TestRobustness(unittest.TestCase):
    def test_dirty_points_are_dropped(self):
        points = [
            [BASE_LNG, BASE_LAT],
            None,
            [BASE_LNG + 0.001],
            "121.59,38.88",
            [float("nan"), BASE_LAT],
            [BASE_LNG + 0.002, BASE_LAT],
            [999.0, BASE_LAT],
            [BASE_LNG + 0.003, BASE_LAT],
        ]
        result = simplify_polyline(points, tolerance_m=6)
        for lng, lat in result:
            self.assertTrue(-180.0 <= lng <= 180.0)
            self.assertTrue(-90.0 <= lat <= 90.0)
            self.assertFalse(math.isnan(lng) or math.isnan(lat))
        self.assertEqual(result[0], [BASE_LNG, BASE_LAT])

    def test_deep_input_does_not_blow_recursion_limit(self):
        """5000 点用递归实现会 RecursionError，这里必须正常返回。"""
        points = [[BASE_LNG + i * 0.00001, BASE_LAT + math.sin(i / 10) * 0.0005] for i in range(5000)]
        result = simplify_polyline(points, tolerance_m=6)
        self.assertGreaterEqual(len(result), 2)
        self.assertLess(len(result), 5000)

    def test_max_points_is_enforced(self):
        points = [[BASE_LNG + i * 0.00001, BASE_LAT + math.sin(i / 5) * 0.001] for i in range(2000)]
        result = simplify_polyline(points, tolerance_m=0.5, max_points=100)
        self.assertLessEqual(len(result), 100)
        self.assertEqual(result[0], [round(points[0][0], 6), round(points[0][1], 6)])
        self.assertEqual(result[-1], [round(points[-1][0], 6), round(points[-1][1], 6)])

    def test_zero_tolerance_keeps_shape_but_still_caps(self):
        points = [[BASE_LNG + i * 0.00001, BASE_LAT + math.sin(i / 5) * 0.001] for i in range(800)]
        result = simplify_polyline(points, tolerance_m=0, max_points=400)
        self.assertLessEqual(len(result), 400)

    def test_coordinates_have_six_decimals(self):
        points = [[BASE_LNG + i * 0.000012345, BASE_LAT + i * 0.00000789] for i in range(50)]
        for lng, lat in simplify_polyline(points, tolerance_m=6):
            self.assertEqual(lng, round(lng, 6))
            self.assertEqual(lat, round(lat, 6))


class TestShapeFidelity(unittest.TestCase):
    def test_deviation_within_tolerance_on_realistic_route(self):
        """模拟城市道路：直行 + 直角转弯 + 轻微抖动，检查最大偏离不超过容差。"""
        points = []
        lng, lat = BASE_LNG, BASE_LAT
        for i in range(60):
            points.append([lng + i * 0.00008, lat + random.uniform(-0.000005, 0.000005)])
        seg2 = points[-1][0]
        for i in range(1, 60):
            points.append([seg2 + random.uniform(-0.000005, 0.000005), lat + i * 0.00008])

        tolerance = 6.0
        simplified = simplify_polyline(points, tolerance_m=tolerance)
        worst = max_deviation(points, simplified)

        self.assertLess(len(simplified), len(points))
        self.assertLessEqual(worst, tolerance * 1.05, f"最大偏离 {worst:.2f}m 超过容差 {tolerance}m")

    def test_compression_ratio_on_road_like_route(self):
        """道路折线的实际压缩率应在 60% 以上，否则这个优化没有意义。"""
        random.seed(42)
        points = []
        lng, lat = BASE_LNG, BASE_LAT
        for i in range(200):
            lng += 0.00006
            lat += random.uniform(-0.00002, 0.00002)
            points.append([lng, lat])
        simplified = simplify_polyline(points, tolerance_m=6)
        ratio = 1 - len(simplified) / len(points)
        self.assertGreater(ratio, 0.6, f"压缩率仅 {ratio:.0%}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
