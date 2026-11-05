# author: szw
"""PDF 分块器的离线测试：不联网、不需要真实 PDF。

分层的意义：抽取（pdfplumber）和分块（纯函数）已经拆开，这里覆盖的是分块这一层，
用构造出来的页面文本就能覆盖页眉页脚、硬换行、跨页、超长段、无标记降级等全部路径。
真实 PDF 的抽取验证放在 scripts/check_pdf_ingest.py，由人工跑。
"""
import unittest

from app.services.pdf_chunk import (
    MAX_CHUNK_CHARS,
    OVERLAP_CHARS,
    chunk_pages,
    chunk_stats,
    cn_to_int,
    detect_day_label,
    normalize_lines,
)

DAY1 = """第一天：星海广场与滨海路
上午：抵达大连后前往星海广场，游览广场与圣亚海洋世界，感受海风。
下午：沿滨海路徒步，途经燕窝岭与北大桥，最终到达老虎滩海洋公园。
晚上：在星海广场附近吃海鲜，人均约一百五十元。"""

DAY2 = """第二天：旅顺口历史一日
上午：前往旅顺口，参观旅顺博物馆与旅顺火车站旧址。
下午：游览白玉山景区，俯瞰旅顺军港全景。
晚上：返回市区，在中山广场周边散步。"""

DAY3 = """第三天：金石滩与返程
上午：前往金石滩国家旅游度假区，游览黄金海岸。
下午：参观金石蜡像馆，之后返回市区准备返程。"""


def page(number, text):
    return {"page": number, "text": text}


class CnNumberTest(unittest.TestCase):
    def test_digits(self):
        self.assertEqual(cn_to_int("3"), 3)
        self.assertEqual(cn_to_int("2"), 2)

    def test_single_cn_digit(self):
        self.assertEqual(cn_to_int("三"), 3)
        self.assertEqual(cn_to_int("两"), 2)

    def test_teens(self):
        self.assertEqual(cn_to_int("十"), 10)
        self.assertEqual(cn_to_int("十二"), 12)
        self.assertEqual(cn_to_int("十五"), 15)

    def test_tens(self):
        self.assertEqual(cn_to_int("二十"), 20)
        self.assertEqual(cn_to_int("二十一"), 21)

    def test_unknown(self):
        self.assertIsNone(cn_to_int(""))
        self.assertIsNone(cn_to_int("廿"))
        self.assertIsNone(cn_to_int("abc"))


class DayLabelTest(unittest.TestCase):
    def test_chinese_numeral(self):
        self.assertEqual(detect_day_label("第一天：星海广场")["day"], 1)
        self.assertEqual(detect_day_label("第三天")["day"], 3)

    def test_arabic(self):
        self.assertEqual(detect_day_label("第2天 旅顺口")["day"], 2)

    def test_bracket_form(self):
        self.assertEqual(detect_day_label("【第三天】金石滩")["day"], 3)

    def test_english_forms(self):
        self.assertEqual(detect_day_label("Day 1：Dalian")["day"], 1)
        self.assertEqual(detect_day_label("DAY 12")["day"], 12)
        self.assertEqual(detect_day_label("D2、旅顺口")["day"], 2)

    def test_date_forms(self):
        result = detect_day_label("10月3日（周三）")
        self.assertIsNotNone(result)
        self.assertIsNone(result["day"])  # 只认边界，推不出第几天
        self.assertIn("10月3日", result["label"])

    def test_iso_date(self):
        self.assertIsNotNone(detect_day_label("2026-10-03 大连"))

    def test_long_date_line_is_not_heading(self):
        # 以日期开头的正文不能被当成段落标题，否则会把一段完整的介绍劈成两半
        line = "3月23日至11月15日，11月16日至3月23日 4:00-6:00 开放时间，门票 50 元"
        self.assertIsNone(detect_day_label(line))

    def test_not_a_heading(self):
        self.assertIsNone(detect_day_label("我们第一天去了星海广场，第二天去了旅顺口，第三天在金石滩。"))
        self.assertIsNone(detect_day_label("一、行程概览"))

    def test_long_line_is_not_heading(self):
        self.assertIsNone(detect_day_label("第一天" + "很长的内容" * 20))


class NormalizeTest(unittest.TestCase):
    def test_repeated_header_is_dropped(self):
        pages = [page(i, f"大连旅游攻略\n正文第{i}页的内容，讲的是不同的景点安排。\n- {i} -")
                 for i in (1, 2, 3)]
        flat = normalize_lines(pages)
        self.assertFalse(any("大连旅游攻略" in line for _, line in flat))
        self.assertFalse(any(line.startswith("- ") for _, line in flat))

    def test_page_number_forms_dropped(self):
        pages = [
            page(1, "第一段正文内容比较长一些。\n第二段正文内容也比较长一些。\n第 1 页 / 共 3 页"),
            page(2, "另一页的正文内容同样足够长。\n还有一行正文内容在这里。\nPage 2 of 3"),
            page(3, "第三页正文内容也足够长了。\n再来一行正文内容补足行数。\n2/3"),
        ]
        flat = normalize_lines(pages)
        text = "\n".join(line for _, line in flat)
        self.assertNotIn("第 1 页", text)
        self.assertNotIn("Page 2", text)
        self.assertNotIn("2/3", text)

    def test_bare_number_at_page_end_dropped_only_there(self):
        # 最后一行是裸数字 → 当页码删掉；中间的裸数字 → 是预算，留下
        body = "上午去星海广场玩了一整个上午。\n1500\n下午沿滨海路散步到老虎滩。\n12"
        flat = normalize_lines([page(1, body)])
        lines = [line for _, line in flat]
        self.assertIn("1500", "\n".join(lines))
        self.assertFalse(any(line.strip() == "12" for line in lines))

    def test_soft_wrap_is_merged(self):
        pages = [page(1, "上午抵达大连后前往星海广场游览，感受海风\n和阳光，下午沿滨海路徒步到老虎滩。")]
        flat = normalize_lines(pages)
        self.assertEqual(len(flat), 1)
        self.assertIn("海风和阳光", flat[0][1])

    def test_list_items_are_not_merged(self):
        pages = [page(1, "今天的安排如下\n- 星海广场\n- 老虎滩\n- 滨海路")]
        flat = normalize_lines(pages)
        self.assertEqual([line for _, line in flat],
                         ["今天的安排如下", "- 星海广场", "- 老虎滩", "- 滨海路"])

    def test_sentence_end_is_not_merged(self):
        pages = [page(1, "上午去了星海广场。\n下午去了老虎滩海洋公园。")]
        flat = normalize_lines(pages)
        self.assertEqual(len(flat), 2)

    def test_empty_page(self):
        self.assertEqual(normalize_lines([page(1, "")]), [])


class ChunkTest(unittest.TestCase):
    def test_three_days_on_one_page(self):
        docs = chunk_pages([page(1, "\n".join([DAY1, DAY2, DAY3]))])
        self.assertEqual(len(docs), 3)
        self.assertEqual([doc.metadata["day"] for doc in docs], [1, 2, 3])
        self.assertTrue(docs[0].page_content.startswith("第一天"))

    def test_day_spanning_two_pages(self):
        pages = [
            page(1, "第一天：星海广场\n上午抵达大连后先去星海广场，感受海风。\n第二天：旅顺口\n上午前往旅顺口参观博物馆。"),
            page(2, "下午游览白玉山景区，俯瞰旅顺军港全景。\n第三天：金石滩\n上午前往金石滩，游览黄金海岸。"),
        ]
        docs = chunk_pages(pages)
        by_day = {doc.metadata["day"]: doc for doc in docs}
        self.assertEqual(by_day[2].metadata["page_start"], 1)
        self.assertEqual(by_day[2].metadata["page_end"], 2)
        self.assertIn("白玉山", by_day[2].page_content)

    def test_metadata_is_passed_through(self):
        docs = chunk_pages([page(1, DAY1)], metadata={"doc_id": "d1", "user_id": "u1"})
        self.assertEqual(docs[0].metadata["doc_id"], "d1")
        self.assertEqual(docs[0].metadata["user_id"], "u1")
        self.assertIn("chunk_index", docs[0].metadata)
        self.assertIn("char_count", docs[0].metadata)

    def test_long_section_is_split_with_overlap(self):
        sentence = "上午前往景区游览，沿途可以看到海岸线与礁石群，风景很好。"
        long_day = "第一天：超长的一天\n" + sentence * 60
        docs = chunk_pages([page(1, long_day)])
        self.assertGreater(len(docs), 1)
        self.assertTrue(all(len(doc.page_content) <= MAX_CHUNK_CHARS for doc in docs))
        # 后一块的开头应该来自前一块的结尾附近（重叠）
        head = docs[1].page_content[:OVERLAP_CHARS].strip()
        self.assertTrue(docs[0].page_content.rstrip().endswith(head[:20])
                        or head[:20] in docs[0].page_content)

    def test_short_section_is_merged(self):
        pages = [page(1, "第一天：星海广场\n上午抵达大连后先去星海广场看海，感受海风。\n下午沿滨海路散步到老虎滩。\n小贴士\n带件外套。")]
        docs = chunk_pages(pages)
        self.assertEqual(len(docs), 1)
        self.assertIn("带件外套", docs[0].page_content)

    def test_short_day_keeps_its_own_chunk(self):
        docs = chunk_pages([page(1, "第一天：抵达与休整\n上午抵达大连后先入住酒店休息。\n第二天：自由活动\n睡到自然醒。")])
        self.assertEqual([doc.metadata["day"] for doc in docs], [1, 2])

    def test_no_day_marker_falls_back_to_paragraphs(self):
        paragraph = "大连是一座海滨城市，市区景点集中在中山区与沙河口区，交通以有轨电车和地铁为主。" * 40
        docs = chunk_pages([page(1, paragraph), page(2, paragraph)])
        self.assertGreater(len(docs), 1)
        self.assertTrue(all(len(doc.page_content) <= MAX_CHUNK_CHARS for doc in docs))
        self.assertTrue(all(doc.metadata["day"] is None for doc in docs))

    def test_chunk_index_is_continuous(self):
        docs = chunk_pages([page(1, "\n".join([DAY1, DAY2, DAY3]))])
        self.assertEqual([doc.metadata["chunk_index"] for doc in docs], list(range(len(docs))))

    def test_content_is_not_lost(self):
        docs = chunk_pages([page(1, "\n".join([DAY1, DAY2, DAY3]))])
        merged = "\n".join(doc.page_content for doc in docs).replace("\n", "")
        for keyword in ("圣亚海洋世界", "旅顺博物馆", "金石蜡像馆", "中山广场"):
            self.assertIn(keyword, merged)

    def test_empty_input(self):
        self.assertEqual(chunk_pages([]), [])
        self.assertEqual(chunk_pages([page(1, "")]), [])
        self.assertEqual(chunk_pages([page(1, "   \n  \n ")]), [])

    def test_stats(self):
        docs = chunk_pages([page(1, "\n".join([DAY1, DAY2, DAY3]))])
        stats = chunk_stats(docs)
        self.assertEqual(stats["chunks"], 3)
        self.assertEqual(stats["days"], 3)
        self.assertEqual(stats["over_limit"], 0)
        self.assertGreater(stats["avg_chars"], 0)

    def test_stats_of_empty(self):
        self.assertEqual(chunk_stats([])["chunks"], 0)


if __name__ == "__main__":
    unittest.main()
