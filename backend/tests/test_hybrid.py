# author: szw
"""混合检索的离线测试：BM25 打分、RRF 融合、降级路径。

不碰真向量库，也不联网。这里能测的都是纯函数，真正"检索得好不好"
要靠 scripts/eval_retrieval.py 对着 123 个目的地量，那是另一回事。
"""
import unittest
from unittest import mock

from langchain_core.documents import Document

from app.config import settings
from app.rag import keyword_index, retriever
from app.rag.keyword_index import KeywordIndex


def make_doc(text, destination=None, doc_id=None):
    meta = {}
    if destination:
        meta["destination"] = destination
    if doc_id:
        meta["doc_id"] = doc_id
    return Document(page_content=text, metadata=meta)


class TokenizeTest(unittest.TestCase):
    def test_drops_single_chars_and_symbols(self):
        tokens = keyword_index.tokenize("三亚 · 攻略 — 好玩")
        self.assertIn("三亚", tokens)
        self.assertNotIn("·", tokens)
        self.assertNotIn("—", tokens)

    def test_keeps_numbers_and_latin(self):
        tokens = keyword_index.tokenize("胜利路410号 人均80元 ABC")
        self.assertIn("410", tokens)
        self.assertIn("abc", tokens)

    def test_empty_input(self):
        self.assertEqual(keyword_index.tokenize(""), [])
        self.assertEqual(keyword_index.tokenize(None), [])


class Bm25Test(unittest.TestCase):
    """打分对不对，只看两件事：该命中的排在前面、高频词不扣分。"""

    DOCS = [
        make_doc("第一市场海鲜排档，人均八十元，早上六点开门", "三亚"),
        make_doc("亚龙湾沙滩细腻，适合清晨散步看海", "三亚"),
        make_doc("故宫门票六十元，建议从午门进", "北京"),
    ]

    def setUp(self):
        self.index = KeywordIndex.build(self.DOCS, [str(i) for i in range(len(self.DOCS))])
        self.index.docs = self.DOCS

    def test_exact_place_name_wins(self):
        # 「第一市场」是地名，向量检索很容易把它和同主题的「亚龙湾」混淆，
        # 关键词检索必须能区分开——这正是引入 BM25 的理由
        hits = self.index.search("第一市场几点开门", top_k=1)
        self.assertEqual(hits[0][0], 0)

    def test_idf_is_never_negative(self):
        # 「攻略」出现在每个文档里（df == N）。原始 BM25 公式此时给负 idf，
        # 命中它的块会被扣分，表现是"越相关排越后"。Lucene 的 +1 修正解决这个。
        docs = [make_doc(f"这是攻略第{i}篇", "三亚") for i in range(5)]
        index = KeywordIndex.build(docs, [str(i) for i in range(5)])
        self.assertGreaterEqual(index._idf("攻略"), 0.0)

    def test_filter_excludes_other_destinations(self):
        # 关键词路也必须能用目的地过滤，否则北京的问题会把三亚的块带进来
        hits = self.index.search(
            "门票", top_k=5,
            allow=lambda pos: (self.index.docs[pos].metadata or {}).get("destination") == "北京")
        self.assertEqual([pos for pos, _ in hits], [2])

    def test_no_match_returns_empty(self):
        self.assertEqual(self.index.search("潜水执照怎么考", top_k=3), [])

    def test_empty_query(self):
        self.assertEqual(self.index.search("", top_k=3), [])
        self.assertEqual(self.index.search("a", top_k=3), [])

    def test_longer_document_does_not_dominate(self):
        # 长度归一：一节 200 字不该因为词多就压过精确匹配的短块
        docs = [
            make_doc("鹿回头山顶看日落最好", "三亚"),
            make_doc("鹿回头 " * 200, "三亚"),
        ]
        index = KeywordIndex.build(docs, ["a", "b"])
        hits = index.search("鹿回头山顶看日落", top_k=2)
        self.assertEqual(hits[0][0], 0, "精确匹配的短块要排在灌水的长块前面")


class CacheKeyTest(unittest.TestCase):
    def test_changes_with_content(self):
        first = keyword_index._collection_key(["1", "2"])
        self.assertNotEqual(first, keyword_index._collection_key(["1", "3"]))

    def test_order_independent(self):
        # Chroma 的返回顺序不保证稳定，指纹不能跟着它变
        self.assertEqual(keyword_index._collection_key(["2", "1"]),
                         keyword_index._collection_key(["1", "2"]))


class RrfTest(unittest.TestCase):
    def test_document_hit_by_both_routes_wins(self):
        # 两路都召回的块分数累加，稳过只在单路排第一的块
        both = make_doc("两路都命中")
        only_vector = make_doc("只在向量路")
        only_keyword = make_doc("只在关键词路")

        fused = retriever._rrf([[both, only_vector], [only_keyword, both]])
        self.assertEqual(fused[0][0].page_content, "两路都命中")

    def test_rank_decay(self):
        docs = [make_doc(f"d{i}") for i in range(3)]
        fused = retriever._rrf([docs])
        self.assertGreater(fused[0][1], fused[1][1])
        self.assertGreater(fused[1][1], fused[2][1])

    def test_duplicate_content_is_merged(self):
        # 两路可能用不同 Document 实例返回同一块，必须按正文合并
        fused = retriever._rrf([[make_doc("一样的正文")], [make_doc("一样的正文")]])
        self.assertEqual(len(fused), 1)
        self.assertAlmostEqual(fused[0][1], 2 / 61, places=6)

    def test_empty_input(self):
        self.assertEqual(retriever._rrf([]), [])
        self.assertEqual(retriever._rrf([[], []]), [])


class MatchesTest(unittest.TestCase):
    def test_in_operator(self):
        self.assertTrue(retriever._matches(
            {"destination": "三亚"}, {"destination": {"$in": ["三亚", "海口"]}}))
        self.assertFalse(retriever._matches(
            {"destination": "北京"}, {"destination": {"$in": ["三亚"]}}))

    def test_equality(self):
        self.assertTrue(retriever._matches({"user_id": "u1"}, {"user_id": "u1"}))
        self.assertFalse(retriever._matches({"user_id": "u2"}, {"user_id": "u1"}))

    def test_missing_field_fails(self):
        # 没有该字段的块在过滤时应该被排除，而不是当成"都匹配"
        self.assertFalse(retriever._matches({}, {"destination": {"$in": ["三亚"]}}))

    def test_no_filter_matches_everything(self):
        self.assertTrue(retriever._matches({}, None))


class KeywordRankingTest(unittest.TestCase):
    def test_returns_empty_when_index_unavailable(self):
        # 用户库没人上传过资料时索引是空的，这里必须安静地返回空，
        # 让混合检索退化成纯向量，而不是让整次规划失败
        with mock.patch("app.rag.keyword_index.get_index", return_value=None):
            self.assertEqual(retriever._keyword_ranking("三亚", 5, "public"), [])

    def test_applies_where_filter(self):
        # 两条都命中「北京」，过滤后只留真正属于北京的块
        docs = [make_doc("北京路在广州越秀区", "三亚"), make_doc("北京的山", "北京")]
        index = KeywordIndex.build(docs, ["a", "b"])
        index.docs = docs
        with mock.patch("app.rag.keyword_index.get_index", return_value=index):
            hits = retriever._keyword_ranking(
                "北京", 5, "public", {"destination": {"$in": ["北京"]}})
        self.assertEqual([d.page_content for d in hits], ["北京的山"])


class HybridRetrieveTest(unittest.TestCase):
    """融合与降级：关键词路挂了不能拖垮检索。"""

    def setUp(self):
        self._dest = mock.patch.object(retriever, "known_destinations", return_value=["三亚"])
        self._dest.start()

    def tearDown(self):
        self._dest.stop()

    def _run(self, hybrid, keyword_hits, vector_hits):
        with mock.patch.object(settings, "hybrid_search", hybrid), \
             mock.patch.object(retriever, "_search", return_value=vector_hits), \
             mock.patch.object(retriever, "_keyword_ranking", return_value=keyword_hits):
            return retriever.retrieve_context("三亚怎么玩", top_k=3, destination="三亚")

    def test_hybrid_keeps_both_routes(self):
        ctx = self._run(
            True,
            [make_doc("关键词路独有的块", "三亚", "guide:三亚")],
            [(make_doc("向量路独有的块", "三亚", "guide:三亚"), 0.4)])
        texts = [d.page_content for d in ctx["docs"]]
        self.assertIn("关键词路独有的块", texts)
        self.assertIn("向量路独有的块", texts)

    def test_vector_only_mode_ignores_keyword_route(self):
        with mock.patch.object(retriever, "_keyword_ranking", return_value=[]) as kw:
            with mock.patch.object(settings, "hybrid_search", False), \
                 mock.patch.object(retriever, "_search",
                                   return_value=[(make_doc("向量块", "三亚"), 0.4)]):
                ctx = retriever.retrieve_context("三亚", top_k=3, destination="三亚")
        kw.assert_not_called()
        self.assertEqual([d.page_content for d in ctx["docs"]], ["向量块"])

    def test_keyword_route_failure_does_not_break_retrieval(self):
        # 索引建不出来（比如缓存文件损坏、Chroma 读不到）时必须静默降级，
        # 让用户照样拿到向量检索的结果——它只是次优，不是不可用
        with mock.patch.object(settings, "hybrid_search", True), \
             mock.patch.object(retriever, "_search",
                               return_value=[(make_doc("向量块", "三亚"), 0.4)]), \
             mock.patch("app.rag.keyword_index.get_index",
                        side_effect=RuntimeError("索引坏了")):
            ctx = retriever.retrieve_context("三亚", top_k=3, destination="三亚")
        self.assertEqual([d.page_content for d in ctx["docs"]], ["向量块"])

    def test_empty_keyword_result_still_returns_vector_docs(self):
        ctx = self._run(True, [], [(make_doc("向量块", "三亚"), 0.4)])
        self.assertEqual([d.page_content for d in ctx["docs"]], ["向量块"])


if __name__ == "__main__":
    unittest.main()
