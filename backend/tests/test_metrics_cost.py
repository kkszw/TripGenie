# author: szw
"""成本折算的离线测试。

计入成本这件事看起来只是几个乘法，但有两个真会算错的地方：
  1. 输入价必须分「缓存命中 / 未命中」，差 50 倍。只按总输入量乘未命中价，
     一次几百字的小调用会被算成几厘钱、几千字上下文的调用被算成几分钱，
     量级就已经不对了。
  2. 价格随时段变（工作日 9-12、14-18 高峰，其余半价），而且一次规划可能跨 18:00。

这里不测真实金额，测的是"公式在这几种边界下不塌"。
"""
import unittest
from datetime import datetime

from app.config import settings
from app.core.metrics import PEAK_WINDOWS, RunMetrics, is_peak_hour


def _metrics() -> RunMetrics:
    return RunMetrics(trace_id="t", thread_id="th")


class PeakHourTest(unittest.TestCase):
    def test_weekday_windows_are_peak(self):
        # 2026-11-05 是周四
        self.assertTrue(is_peak_hour(datetime(2026, 11, 5, 9, 0)))
        self.assertTrue(is_peak_hour(datetime(2026, 11, 5, 11, 59)))
        self.assertTrue(is_peak_hour(datetime(2026, 11, 5, 14, 0)))
        self.assertTrue(is_peak_hour(datetime(2026, 11, 5, 17, 59)))

    def test_lunch_break_and_evening_are_offpeak(self):
        self.assertFalse(is_peak_hour(datetime(2026, 11, 5, 12, 0)))
        self.assertFalse(is_peak_hour(datetime(2026, 11, 5, 13, 59)))
        self.assertFalse(is_peak_hour(datetime(2026, 11, 5, 18, 0)))
        self.assertFalse(is_peak_hour(datetime(2026, 11, 5, 3, 30)))

    def test_weekend_is_offpeak_all_day(self):
        # 2026-11-07 是周六
        self.assertFalse(is_peak_hour(datetime(2026, 11, 7, 10, 0)))
        self.assertFalse(is_peak_hour(datetime(2026, 11, 8, 15, 0)))

    def test_windows_are_ordered(self):
        # 写反了会导致永远判成空闲，成本一路偏低且没人发现
        for start, end in PEAK_WINDOWS:
            self.assertLess(start, end)


class LlmCostTest(unittest.TestCase):
    def test_cache_hit_is_much_cheaper_than_miss(self):
        hit = _metrics()
        hit.add_llm("plan", "deepseek-flash", input_tokens=3000, output_tokens=0,
                    cache_hit_tokens=3000)
        miss = _metrics()
        miss.add_llm("plan", "deepseek-flash", input_tokens=3000, output_tokens=0,
                     cache_hit_tokens=0)
        self.assertGreater(miss.summary()["llm"]["cost_cny"],
                           hit.summary()["llm"]["cost_cny"] * 20)

    def test_cache_hit_never_exceeds_input(self):
        # 服务端偶尔会给出大于 input 的 cache_read（跨调用的统计），
        # 直接用会算出负的未命中量
        m = _metrics()
        m.add_llm("plan", "deepseek-flash", input_tokens=100, output_tokens=10,
                  cache_hit_tokens=999)
        llm = m.summary()["llm"]
        self.assertEqual(llm["cache_hit_tokens"], 100)
        self.assertGreaterEqual(llm["cost_cny"], 0)

    def test_output_tokens_are_charged(self):
        m = _metrics()
        m.add_llm("plan", "deepseek-flash", input_tokens=0, output_tokens=1_000_000,
                  cache_hit_tokens=0)
        # 显式固定成高峰：add_llm 会按"当前几点"实时判时段，跑测试的时间不同
        # 结果就差一倍（白天跑通过、晚上跑失败）。
        m.llm_calls[0].peak = True
        expected = settings.llm_output_price
        self.assertAlmostEqual(m.summary()["llm"]["cost_cny"], expected, places=6)

    def test_offpeak_is_half_price(self):
        m = _metrics()
        m.add_llm("plan", "deepseek-flash", input_tokens=1_000_000, output_tokens=0,
                  cache_hit_tokens=0)
        call = m.llm_calls[0]
        call.peak = True
        peak = m._cost_cny()
        call.peak = False
        offpeak = m._cost_cny()
        self.assertAlmostEqual(offpeak / peak, settings.llm_offpeak_ratio, places=6)

    def test_zero_price_config_reports_zero_cost(self):
        # 单价配 0 是"只统计 token"的开关，不能算出 nan 或负数
        m = _metrics()
        m.add_llm("plan", "deepseek-flash", input_tokens=1000, output_tokens=100)
        saved = (settings.llm_input_price, settings.llm_output_price,
                 settings.llm_cache_hit_price)
        try:
            settings.llm_input_price = settings.llm_output_price = 0.0
            settings.llm_cache_hit_price = 0.0
            self.assertEqual(m.summary()["llm"]["cost_cny"], 0.0)
        finally:
            (settings.llm_input_price, settings.llm_output_price,
             settings.llm_cache_hit_price) = saved

    def test_cache_ratio(self):
        m = _metrics()
        m.add_llm("plan", "deepseek-flash", input_tokens=1000, output_tokens=0,
                  cache_hit_tokens=750)
        self.assertAlmostEqual(m.summary()["llm"]["cache_hit_ratio"], 0.75, places=3)

    def test_no_calls_is_zero(self):
        llm = _metrics().summary()["llm"]
        self.assertEqual(llm["cost_cny"], 0.0)
        self.assertEqual(llm["cache_hit_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
