# author: szw
"""目的地解析（geo.py）的单元测试。

背景：山东那次翻车的根因就在这个文件对应的逻辑上——`'山东省'.split('省')[1]`
拿到空串，城市约束丢失，高德退回全国匹配，"芙蓉街"从济南跑到云南红河州。
修完之后没有测试兜着，任何人改错一个省份别名，路线又得横跨全国。

这里锁三件事：
  1. 34 个省级行政区的全名/简称都能解析，且 query_value 一律是 6 位 adcode
     （不是中文省名——高德对省名的宽容度不稳定，用 adcode 才不会静默退化）；
  2. 城市/省/未知三档的粒度和偏移上限；
  3. 球面距离与偏移校验。

跑法：
    cd backend && python -m unittest tests.test_geo -v
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.geo import (  # noqa: E402
    MAX_OFFSET_KM,
    MUNICIPALITIES,
    PROVINCES,
    Region,
    haversine_km,
    is_offset_ok,
    parse_region,
)

# 实测坐标，用来验证距离量级（不是精确值，误差来自坐标取整到区中心）
JINAN = (117.1205, 36.6519)
QINGDAO = (120.3826, 36.0671)
BEIJING = (116.4074, 39.9042)
SHANGHAI = (121.4737, 31.2304)


class ProvinceTableTest(unittest.TestCase):
    """省份表的完整性与唯一性。少一行、重复一个 adcode，都该在这里红。"""

    def test_all_34_regions_present(self):
        self.assertEqual(len(PROVINCES), 34, "省级行政区应为 34 个")

    def test_adcode_is_six_digits_and_unique(self):
        codes = [code for _, _, code in PROVINCES]
        self.assertEqual(len(codes), len(set(codes)), "adcode 有重复")
        for code in codes:
            with self.subTest(code=code):
                self.assertRegex(code, r"^\d{6}$")
                self.assertTrue(code.endswith("0000"))

    def test_full_name_resolves_to_province_scope(self):
        for full, _short, code in PROVINCES:
            with self.subTest(full=full):
                region = parse_region(full)
                self.assertEqual(region.province, full)
                if full in MUNICIPALITIES:
                    # 直辖市同时是城市：粒度收窄到 city，query_value 用城市名
                    self.assertEqual(region.scope, "city")
                    self.assertEqual(region.query_value, MUNICIPALITIES[full])
                else:
                    # 只有省份时用 adcode 查询，不能用中文名
                    self.assertEqual(region.scope, "province")
                    self.assertEqual(region.query_value, code)

    def test_short_name_resolves_to_full_name(self):
        for full, short, _code in PROVINCES:
            with self.subTest(short=short):
                self.assertEqual(parse_region(short).province, full)


class ProvinceOnlyRegressionTest(unittest.TestCase):
    """「帮我规划山东三天旅行」这一类输入：必须解析成省级，且带约束。"""

    def test_province_only_scope(self):
        region = parse_region("山东省")
        self.assertEqual(region.scope, "province")
        self.assertIsNone(region.city)
        self.assertTrue(region.is_province_only)
        self.assertEqual(region.max_offset_km, MAX_OFFSET_KM["province"])

    def test_province_query_is_adcode_not_chinese_name(self):
        """这是那个 bug 的守门员：一旦有人把 query_value 改回 '山东省'，这条会红。
        传中文省名给高德会被静默忽略，退回全国匹配，而且接口不报错。"""
        region = parse_region("山东省")
        self.assertEqual(region.query_value, "370000")
        self.assertNotEqual(region.query_value, "山东省")
        self.assertNotEqual(region.query_value, "山东")

    def test_short_province_name_is_normalized(self):
        """用户只写「山东」，也要补成全名再查 adcode。"""
        region = parse_region("山东")
        self.assertEqual(region.province, "山东省")
        self.assertEqual(region.query_value, "370000")

    def test_china_prefix_is_stripped(self):
        region = parse_region("中国山东省")
        self.assertEqual(region.scope, "province")
        self.assertEqual(region.query_value, "370000")

    def test_whitespace_is_trimmed(self):
        self.assertEqual(parse_region("  山东省  ").query_value, "370000")


class CityScopeTest(unittest.TestCase):
    """给了具体城市时，约束要收窄到城市。"""

    def test_province_plus_city(self):
        region = parse_region("山东省青岛市")
        self.assertEqual(region.city, "青岛")
        self.assertEqual(region.province, "山东省")
        self.assertEqual(region.scope, "city")
        self.assertEqual(region.query_value, "青岛")
        self.assertEqual(region.max_offset_km, MAX_OFFSET_KM["city"])

    def test_city_suffix_is_stripped(self):
        self.assertEqual(parse_region("青岛市").city, "青岛")
        self.assertEqual(parse_region("青岛").city, "青岛")

    def test_city_has_no_province(self):
        region = parse_region("青岛市")
        self.assertIsNone(region.province)
        self.assertEqual(region.label, "青岛")

    def test_district_suffix_is_dropped(self):
        """「济南市历下区」取到市一级就够了，高德的 city 参数不认区。"""
        self.assertEqual(parse_region("济南市历下区").city, "济南")

    def test_autonomous_prefecture_keeps_full_name(self):
        """自治州去掉民族名反而不认（'大理' 与 '大理白族自治州' 是两回事）。"""
        self.assertEqual(parse_region("大理白族自治州").city, "大理白族自治州")
        self.assertEqual(parse_region("恩施土家族苗族自治州").city, "恩施土家族苗族自治州")

    def test_municipality_is_city_scope(self):
        """直辖市既是省级也是城市：用户说「北京市」是要去北京城区，
        不能当成「只给了省份」去按省内多城处理。"""
        region = parse_region("北京市")
        self.assertEqual(region.scope, "city")
        self.assertEqual(region.city, "北京")
        self.assertEqual(region.query_value, "北京")

    def test_municipality_short_name(self):
        region = parse_region("上海")
        self.assertEqual(region.scope, "city")
        self.assertEqual(region.city, "上海")


class UnknownScopeTest(unittest.TestCase):
    def test_empty(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                region = parse_region(value)
                self.assertEqual(region.scope, "unknown")
                self.assertIsNone(region.query_value)
                self.assertEqual(region.max_offset_km, MAX_OFFSET_KM["unknown"])

    def test_bare_place_name_falls_back_to_city_scope(self):
        """宽松兜底：没有行政区后缀的字符串按"城市"处理。

        这是刻意的——调用方传进来的是目的地，不是单个景点。但代价是
        parse_region("芙蓉街") 会得到 city="芙蓉街"，所以**不要把景点名传给它**，
        景点坐标查询要走 map_data.geocode_attraction 的区域参数。
        """
        self.assertEqual(parse_region("芙蓉街").scope, "city")

    @unittest.expectedFailure
    def test_known_issue_city_sharing_province_name(self):
        """已知缺陷：省名与市名同字时（吉林市 / 海南藏族自治州）会被拆坏。

        parse_region("吉林市") 先用简称「吉林」剥出吉林省，剩下「市」，
        于是 city 变成「市」。修法是：剥省名之前先看整串是否以
        「省名 + 市/州」结尾，或者要求剥完剩余部分长度 ≥ 2。

        修好后这条会变成 unexpected success，提醒把这个标记删掉。
        """
        self.assertEqual(parse_region("吉林市").city, "吉林")


class HaversineTest(unittest.TestCase):
    """距离量级：用来判断一个坐标"是不是目的地那个地方"。"""

    def test_same_point_is_zero(self):
        self.assertAlmostEqual(haversine_km(JINAN, JINAN), 0.0, places=6)

    def test_is_symmetric(self):
        self.assertAlmostEqual(haversine_km(JINAN, QINGDAO), haversine_km(QINGDAO, JINAN), places=6)

    def test_jinan_to_qingdao(self):
        """实测约 299km——正好卡在城市 150km 上限之外，是"省内跨城"的典型样本。"""
        distance = haversine_km(JINAN, QINGDAO)
        self.assertGreater(distance, 280)
        self.assertLess(distance, 320)

    def test_beijing_to_shanghai(self):
        distance = haversine_km(BEIJING, SHANGHAI)
        self.assertGreater(distance, 1040)
        self.assertLess(distance, 1100)

    def test_accepts_string_coordinates(self):
        """高德返回的经纬度可能被序列化成字符串，不能因此抛 TypeError。"""
        self.assertAlmostEqual(
            haversine_km(("117.1205", "36.6519"), ("120.3826", "36.0671")),
            haversine_km(JINAN, QINGDAO),
            places=6,
        )

    def test_antipodal_does_not_overflow(self):
        """对跖点浮点误差会让 sqrt 的参数略大于 1，必须被 min(1.0, h) 兜住。"""
        distance = haversine_km((0.0, 0.0), (180.0, 0.0))
        self.assertTrue(math.isfinite(distance))
        self.assertAlmostEqual(distance, math.pi * 6371.0, places=3)


class OffsetCheckTest(unittest.TestCase):
    """is_offset_ok：坐标离目的地中心太远就不可信，改用估算位置。"""

    def test_no_center_always_passes(self):
        """校验是兜底手段，不能因为它自己拿不到中心点就把正常景点全废掉。"""
        ok, distance = is_offset_ok(QINGDAO, None, Region())
        self.assertTrue(ok)
        self.assertEqual(distance, 0.0)

    def test_city_scope_rejects_cross_city(self):
        """济南到青岛约 299km，超出城市级 150km，必须判为不可信。"""
        region = Region(city="青岛", province="山东省", scope="city")
        ok, distance = is_offset_ok(JINAN, QINGDAO, region)
        self.assertFalse(ok)
        self.assertGreater(distance, 150)

    def test_province_scope_allows_cross_city(self):
        """同样是 299km，「山东省」这个粒度里是合理的（济南→青岛）。"""
        region = Region(province="山东省", scope="province")
        ok, _distance = is_offset_ok(JINAN, QINGDAO, region)
        self.assertTrue(ok)

    def test_city_scope_allows_nearby(self):
        region = Region(city="青岛", province="山东省", scope="city")
        ok, _distance = is_offset_ok(QINGDAO, QINGDAO, region)
        self.assertTrue(ok)

    def test_province_scope_rejects_far_away(self):
        """省粒度 600km：北京到上海 1068km，跨省了，判不可信。"""
        region = Region(province="山东省", scope="province")
        ok, _distance = is_offset_ok(BEIJING, SHANGHAI, region)
        self.assertFalse(ok)

    def test_unknown_scope_threshold(self):
        """未知目的地用 300km 的中间档，而不是无限放行。"""
        region = Region()
        self.assertEqual(region.max_offset_km, 300.0)
        self.assertTrue(is_offset_ok(JINAN, QINGDAO, region)[0])      # 299km < 300
        self.assertFalse(is_offset_ok(BEIJING, SHANGHAI, region)[0])  # 1068km


if __name__ == "__main__":
    unittest.main(verbosity=2)
