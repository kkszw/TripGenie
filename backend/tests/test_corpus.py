# author: szw
"""公共语料库构建的测试。

三块：
  1. 文件名解析（纯函数）—— 这批 125 份的命名有坑，重名和天数后缀都在这里
  2. extract 的断点续传 —— 重跑不该重新 OCR，那要 50 分钟
  3. 检索的目的地过滤 —— 语料从 1 份变 125 份后，不过滤就会拿回外地的段落

全部不联网、不调模型：extract 里的抽取器和检索里的向量查询都被替换掉。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_corpus  # noqa: E402
from app.rag import retriever  # noqa: E402
from app.services.corpus_clean import clean_pages, is_noise_line  # noqa: E402
from app.services.corpus_naming import (  # noqa: E402
    corpus_stem,
    parse_destination,
    slugify,
)


class DestinationParseTest(unittest.TestCase):
    """文件名 → 目的地。"""

    def test_strips_prefix_and_suffix(self):
        self.assertEqual(parse_destination("我是驴友-三亚旅游攻略.pdf"), "三亚")
        self.assertEqual(parse_destination("我是驴友-丽江攻略.pdf"), "丽江")
        self.assertEqual(parse_destination("我是驴友-北京.pdf"), "北京")

    def test_strips_day_count(self):
        # 「八日」和「12日」都是天数标记，不剥掉就会变成目的地的一部分，
        # 用户问「厦门」时那句「厦门八日」永远匹配不上
        self.assertEqual(parse_destination("我是驴友-厦门八日旅游攻略.pdf"), "厦门")
        self.assertEqual(parse_destination("我是驴友-台湾12日旅游攻略.pdf"), "台湾")
        self.assertEqual(parse_destination("我是驴友-香格里拉9日.pdf"), "香格里拉")

    def test_keeps_place_names_that_look_like_suffix(self):
        # 「日照」是城市名，不是「日」后缀 —— 这个在 125 份里真实存在
        self.assertEqual(parse_destination("我是驴友-日照旅游攻略.pdf"), "日照")
        # 景区名里的「山」「湖」不能被当成噪声剥掉
        self.assertEqual(parse_destination("我是驴友-三清山.pdf"), "三清山")
        self.assertEqual(parse_destination("我是驴友-千岛湖.pdf"), "千岛湖")
        self.assertEqual(parse_destination("我是驴友-泸沽湖.pdf"), "泸沽湖")

    def test_multi_word_destination_survives(self):
        self.assertEqual(parse_destination("我是驴友-香港购物旅游攻略.pdf"), "香港购物")
        self.assertEqual(parse_destination("我是驴友-稻城亚丁.pdf"), "稻城亚丁")

    def test_unknown_naming_falls_back_to_stem(self):
        # 提取不出来时返回原文，而不是空串 —— 空串会让检索过滤静默失效
        self.assertEqual(parse_destination("随便一个文件.pdf"), "随便一个文件")


class CorpusStemTest(unittest.TestCase):
    """文件名 → 唯一标识。重名目的地必须能区分开。"""

    def test_duplicate_destinations_get_distinct_stems(self):
        # 这两份的 destination 都是「厦门」，落盘和 doc_id 必须不同，
        # 否则后抽的覆盖先抽的、重建时互相删块
        self.assertEqual(parse_destination("我是驴友-厦门八日旅游攻略.pdf"), "厦门")
        self.assertEqual(parse_destination("我是驴友-厦门旅游攻略.pdf"), "厦门")
        self.assertNotEqual(
            corpus_stem("我是驴友-厦门八日旅游攻略.pdf"),
            corpus_stem("我是驴友-厦门旅游攻略.pdf"),
        )

    def test_stem_matches_destination_when_unambiguous(self):
        self.assertEqual(corpus_stem("我是驴友-三亚旅游攻略.pdf"), "三亚")

    def test_slug_replaces_path_separators(self):
        self.assertEqual(slugify("香港/澳门"), "香港_澳门")
        self.assertEqual(slugify("  "), "unnamed")

    def test_slug_is_length_capped(self):
        # Windows 单段路径 255 字符，语料文件名还有前缀和后缀
        self.assertLessEqual(len(slugify("长" * 200)), 40)


class ExtractResumeTest(unittest.TestCase):
    """extract 的断点续传：OCR 要 50 分钟，重跑不能白费。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pdf = self.tmp / "我是驴友-三亚旅游攻略.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 fake")
        self.out = self.tmp / "corpus"

        # 抽取器被替换掉：这里测的是编排逻辑，不是 OCR 本身
        self.extract_calls = 0

        def fake_extract(_path, **_kwargs):
            self.extract_calls += 1
            return {
                "pages": [{"page": 1, "text": "三亚的海很蓝。" * 40}],
                "mode": "text",
                "quality": {"rare_ratio": 0.0, "de_ratio": 0.03},
                "text_layer_quality": {},
            }

        self._patch_extract = mock.patch.object(
            build_corpus, "extract_document", side_effect=fake_extract)
        self._patch_hash = mock.patch.object(
            build_corpus, "file_hash", return_value="hash-abc")
        self._patch_extract.start()
        self._patch_hash.start()

    def tearDown(self):
        self._patch_extract.stop()
        self._patch_hash.stop()

    def test_first_run_writes_json(self):
        result = build_corpus.extract_one(self.pdf, self.out)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["destination"], "三亚")

        target = self.out / "三亚.json"
        self.assertTrue(target.exists())
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data["destination"], "三亚")
        self.assertEqual(data["slug"], "三亚")
        self.assertEqual(data["pages"][0]["page"], 1)

    def test_second_run_skips_and_does_not_extract(self):
        build_corpus.extract_one(self.pdf, self.out)
        self.assertEqual(self.extract_calls, 1)

        result = build_corpus.extract_one(self.pdf, self.out)
        self.assertEqual(result["status"], "skip")
        self.assertEqual(self.extract_calls, 1, "已抽取的文件不该重新 OCR")

    def test_changed_content_triggers_reextract(self):
        build_corpus.extract_one(self.pdf, self.out)
        # 内容变了（哈希变了）就该重抽，不能因为文件名相同就跳过
        with mock.patch.object(build_corpus, "file_hash", return_value="hash-new"):
            result = build_corpus.extract_one(self.pdf, self.out)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.extract_calls, 2)

    def test_force_reextracts(self):
        build_corpus.extract_one(self.pdf, self.out)
        result = build_corpus.extract_one(self.pdf, self.out, force=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.extract_calls, 2)

    def test_corrupt_json_is_treated_as_missing(self):
        # 上次跑到一半被 Ctrl-C 打断，留下半截文件
        build_corpus.extract_one(self.pdf, self.out)
        (self.out / "三亚.json").write_text("{ broken", encoding="utf-8")
        result = build_corpus.extract_one(self.pdf, self.out)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.extract_calls, 2)

    def test_old_version_triggers_reextract(self):
        build_corpus.extract_one(self.pdf, self.out)
        target = self.out / "三亚.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        data["version"] = 0
        target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        result = build_corpus.extract_one(self.pdf, self.out)
        self.assertEqual(result["status"], "ok")

    def test_extract_failure_is_reported_not_raised(self):
        # 单份坏文件不能让整批 125 份中断
        with mock.patch.object(build_corpus, "extract_document",
                               side_effect=RuntimeError("PDF 已加密")):
            result = build_corpus.extract_one(self.pdf, self.out)
        self.assertEqual(result["status"], "fail")
        self.assertIn("PDF 已加密", result["reason"])

    def test_empty_pages_is_failure(self):
        with mock.patch.object(build_corpus, "extract_document",
                               return_value={"pages": [], "mode": "text"}):
            result = build_corpus.extract_one(self.pdf, self.out)
        self.assertEqual(result["status"], "fail")


class CorpusCleanTest(unittest.TestCase):
    """入库清洗。原则是宁可漏掉噪声，也不能删正常句子。"""

    def test_other_destination_title_is_removed(self):
        # 三亚那份文档的页脚混进了「桂林·攻略」——模板残留
        self.assertTrue(is_noise_line("桂林·攻略", "三亚"))
        self.assertTrue(is_noise_line("丽江·攻略", "昆明"))

    def test_own_destination_title_is_kept(self):
        # 「三亚·攻略」是本文档自己的封面标题
        self.assertFalse(is_noise_line("三亚·攻略", "三亚"))

    def test_normal_chinese_repetition_is_kept(self):
        # 这几条都曾被更激进的规则误杀过，写成用例钉住
        kept = [
            ("一步一步地向石林深处走", "云南"),          # 行首整词重复
            ("空空荡荡的广场，咿咿呀呀唱着绍剧", "乌镇"),  # 连续叠字
            ("喝喝茶，散散心，听听枫泾人的枫泾话", "上海"),  # 多个叠字
            ("上海，去过无数次的城市，每每匆匆而来", "上海"),
        ]
        for line, destination in kept:
            self.assertFalse(is_noise_line(line, destination), line)

    def test_interleaved_boilerplate_is_kept_on_purpose(self):
        # 这行确实是模板残留，但它和「一步一步」在正则层面无法区分，
        # 按「不误删」的优先级保留。写成用例是为了让这个取舍可见。
        self.assertFalse(
            is_noise_line("川藏川藏南南线线十上十日海日攻略··攻攻略略", "上海"))

    def test_without_destination_nothing_is_removed(self):
        self.assertFalse(is_noise_line("桂林·攻略", None))

    def test_clean_pages_preserves_page_numbers(self):
        pages = [{"page": 7, "text": "正文一行\n桂林·攻略\n正文二行"}]
        cleaned = clean_pages(pages, "三亚")
        self.assertEqual(cleaned[0]["page"], 7, "页码不能丢，否则溯源全错")
        self.assertNotIn("桂林·攻略", cleaned[0]["text"])
        self.assertIn("正文一行", cleaned[0]["text"])


class BuildDocumentsTest(unittest.TestCase):
    """语料 → 检索块，元数据必须带 destination 和稳定的 doc_id。"""

    def _payload(self, destination, slug):
        return {
            "destination": destination,
            "slug": slug,
            "source_file": f"我是驴友-{slug}.pdf",
            "pages": [{"page": index, "text": f"{destination}第{index}页的正文" * 30}
                      for index in range(1, 4)],
        }

    def test_chunks_carry_destination(self):
        docs = build_corpus.build_documents(self._payload("三亚", "三亚"))
        self.assertTrue(docs)
        for doc in docs:
            self.assertEqual(doc.metadata["destination"], "三亚")
            self.assertEqual(doc.metadata["scope"], "public")

    def test_duplicate_destination_gets_distinct_doc_ids(self):
        # 厦门两份：destination 相同（过滤时要一起召回），doc_id 不同
        # （重建时各删各的，不能互相覆盖）
        first = build_corpus.build_documents(self._payload("厦门", "厦门"))
        second = build_corpus.build_documents(self._payload("厦门", "厦门八日"))
        self.assertEqual(first[0].metadata["destination"],
                         second[0].metadata["destination"])
        self.assertNotEqual(first[0].metadata["doc_id"],
                            second[0].metadata["doc_id"])


class IndexResumeTest(unittest.TestCase):
    """index 的断点续传。

    这不是为了省时间，是为了省钱。全量嵌入要调几百次接口，跑到一半被
    额度或欠费掐断是常态；没有续传的话重跑会把已入库的几百块重新算一遍。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "corpus"
        self.out.mkdir()
        for slug in ("三亚", "丽江"):
            (self.out / f"{slug}.json").write_text(
                json.dumps(self._payload(slug), ensure_ascii=False), encoding="utf-8")

        # 向量库整层替换：这里测的是「该不该重新写」，不是写入本身
        self.added = []
        self.deleted = []
        self._patches = [
            mock.patch("app.rag.vector_store.add_documents",
                       side_effect=lambda docs, scope: self.added.append(len(docs))),
            mock.patch("app.rag.vector_store.delete_documents",
                       side_effect=lambda where, scope: self.deleted.append(where)),
            mock.patch("app.rag.vector_store.count_documents", return_value=0),
            mock.patch("app.rag.vector_store.get_collection_stats", return_value=0),
            mock.patch("app.rag.vector_store.reset_collection"),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in self._patches:
            patch.stop()

    @staticmethod
    def _payload(slug: str) -> dict:
        lines = []
        for day in (1, 2, 3):
            lines.append(f"D{day} 第{day}天的行程安排")
            lines.append("今天走了很多地方，风景很好。" * 12)
        text = "\n".join(lines)
        return {
            "version": build_corpus.CORPUS_VERSION,
            "destination": slug,
            "slug": slug,
            "source_file": f"我是驴友-{slug}旅游攻略.pdf",
            "pages": [{"page": 1, "text": text}],
            "pages_count": 1,
            "chars": len(text),
            "mode": "text",
        }

    def _args(self, **kwargs):
        args = mock.Mock(out=str(self.out), only="", rebuild=False, skip_existing=True)
        for key, value in kwargs.items():
            setattr(args, key, value)
        return args

    def _expected_blocks(self, slug: str) -> int:
        return len(build_corpus.build_documents(self._payload(slug)))

    def test_skips_document_already_indexed(self):
        expected = self._expected_blocks("三亚")
        with mock.patch("app.rag.vector_store.count_documents", return_value=expected):
            build_corpus.cmd_index(self._args(only="三亚"))
        self.assertEqual(self.added, [], "块数一致就不该再花一次嵌入的钱")
        self.assertEqual(self.deleted, [])

    def test_rewrites_half_written_document(self):
        # 上一轮写到一半被掐断：库里有 1 块，这篇应该是 3 块
        expected = self._expected_blocks("三亚")
        self.assertGreater(expected, 1)
        with mock.patch("app.rag.vector_store.count_documents", return_value=1):
            build_corpus.cmd_index(self._args(only="三亚"))
        self.assertEqual(sum(self.added), expected, "半截的文档要整份重写")
        self.assertTrue(self.deleted, "重写前得先删掉残留块")

    def test_without_flag_every_document_is_rewritten(self):
        build_corpus.cmd_index(self._args(only="三亚", skip_existing=False))
        self.assertEqual(len(self.deleted), 1, "不带 --skip-existing 时保持幂等重写")
        self.assertGreater(sum(self.added), 0)

    def test_rebuild_resets_and_ignores_resume(self):
        build_corpus.cmd_index(self._args(only="", rebuild=True, skip_existing=True))
        import app.rag.vector_store as vs
        self.assertTrue(vs.reset_collection.called, "rebuild 必须先清空公共库")
        self.assertEqual(vs.count_documents.called, False,
                         "库刚被清空，再查每份语料有没有入库是纯浪费")

    def test_chunk_count_change_triggers_rewrite(self):
        # 调了分块参数：库里 2 块、这次分出 3 块 —— 必须重写，不能因为
        # 「有块」就跳过，否则新分块规则对这份语料永远不生效
        expected = self._expected_blocks("三亚")
        self.assertNotEqual(expected, 2)
        with mock.patch("app.rag.vector_store.count_documents", return_value=2):
            build_corpus.cmd_index(self._args(only="三亚"))
        self.assertEqual(sum(self.added), expected)
        self.assertTrue(self.deleted)


class _FakeDoc:
    """只带 metadata 的最小 Document 替身。"""


def make_doc(text: str, destination: str = "", doc_id: str = ""):
    from langchain_core.documents import Document

    return Document(page_content=text,
                    metadata={"destination": destination, "doc_id": doc_id,
                              "source": f"{destination}.pdf", "scope": "public"})


class DestinationFilterTest(unittest.TestCase):
    """检索侧的目的地过滤。全部用假 _search，不碰向量库。"""

    def setUp(self):
        # 目的地列表是从向量库聚合来的，测试里不该碰真库。
        # 用 patch 而不是直接改 _destinations：缓存现在跟着库的代际号失效，
        # 只改值是会被下一轮重算覆盖掉的。
        self._patcher = mock.patch.object(
            retriever, "known_destinations",
            return_value=["大连", "沈阳", "九寨沟", "香港", "香港购物"])
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_exact_match(self):
        self.assertEqual(retriever.match_destinations("沈阳"), ["沈阳"])

    def test_query_is_more_specific_than_library(self):
        # 用户说「香港离岛」，香港攻略里也有一整节讲离岛，一起召回
        self.assertIn("香港", retriever.match_destinations("香港离岛"))

    def test_library_is_more_specific_than_query(self):
        # 用户说「香港」，库里「香港购物」也要召回
        matched = retriever.match_destinations("香港")
        self.assertIn("香港", matched)
        self.assertIn("香港购物", matched)

    def test_unknown_destination_matches_nothing(self):
        self.assertEqual(retriever.match_destinations("拉萨"), [])
        self.assertEqual(retriever.match_destinations(None), [])
        self.assertEqual(retriever.match_destinations(""), [])

    def test_filtered_search_returns_only_local(self):
        calls = []

        def fake_search(query, k, scope, where=None):
            calls.append(where)
            if where:
                return [(make_doc(f"沈阳景点{i}", "沈阳", "guide:沈阳"), 0.5 + i / 100)
                        for i in range(3)]
            return [(make_doc("三亚的海", "三亚", "guide:三亚"), 0.3)]

        with mock.patch.object(retriever, "_search", side_effect=fake_search):
            ctx = retriever.retrieve_context("沈阳怎么玩", top_k=3,
                                             destination="沈阳")

        self.assertTrue(calls[0], "第一次检索必须带目的地过滤")
        self.assertEqual(calls[0]["destination"], {"$in": ["沈阳"]})
        destinations = [d.metadata["destination"] for d in ctx["docs"]]
        self.assertEqual(destinations, ["沈阳", "沈阳", "沈阳"])

    def test_falls_back_to_whole_library_when_short(self):
        # 本地只有 1 条，top_k=3 要用外地补齐（「从成都怎么去九寨沟」这类问题）
        def fake_search(query, k, scope, where=None):
            if where:
                return [(make_doc("沈阳故宫", "沈阳", "guide:沈阳"), 0.5)]
            return [(make_doc("沈阳故宫", "沈阳", "guide:沈阳"), 0.5),
                    (make_doc("成都的交通", "成都", "guide:成都"), 0.7)]

        with mock.patch.object(retriever, "_search", side_effect=fake_search):
            ctx = retriever.retrieve_context("沈阳怎么玩", top_k=3,
                                             destination="沈阳")

        destinations = [d.metadata["destination"] for d in ctx["docs"]]
        self.assertEqual(destinations[0], "沈阳", "本地块必须排在前面")
        self.assertIn("成都", destinations)

    def test_unknown_destination_uses_whole_library(self):
        def fake_search(query, k, scope, where=None):
            if where:
                raise AssertionError("匹配不到目的地时不该带过滤条件")
            return [(make_doc("库里的内容", "三亚", "guide:三亚"), 0.4)]

        with mock.patch.object(retriever, "_search", side_effect=fake_search):
            ctx = retriever.retrieve_context("拉萨怎么玩", top_k=3,
                                             destination="拉萨")
        self.assertEqual(len(ctx["docs"]), 1)

    def test_filter_error_falls_back_instead_of_crashing(self):
        # Chroma 过滤语法出错 / 字段缺失都不能让整次规划失败
        def fake_search(query, k, scope, where=None):
            if where:
                raise ValueError("unsupported where")
            return [(make_doc("兜底结果", "三亚", "guide:三亚"), 0.4)]

        with mock.patch.object(retriever, "_search", side_effect=fake_search):
            ctx = retriever.retrieve_context("沈阳怎么玩", top_k=3,
                                             destination="沈阳")
        self.assertEqual(len(ctx["docs"]), 1)

    def test_user_documents_still_merged(self):
        def fake_search(query, k, scope, where=None):
            if scope == "user":
                return [(make_doc("我自己传的行程", "", "uuid-1"), 0.4)]
            if where:
                return [(make_doc("沈阳故宫", "沈阳", "guide:沈阳"), 0.5)]
            return []

        with mock.patch.object(retriever, "_search", side_effect=fake_search):
            ctx = retriever.retrieve_context("沈阳怎么玩", top_k=3,
                                             user_id="u1", destination="沈阳")

        texts = [d.page_content for d in ctx["docs"]]
        self.assertIn("我自己传的行程", texts)


if __name__ == "__main__":
    unittest.main()
