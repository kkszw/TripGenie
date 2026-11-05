# author: szw
"""用户资料入库（knowledge_base + /api/knowledge）的单元测试。

这条链路的失败方式都很安静，所以每一段都要钉住：
  - 校验漏了 → 用户能传上来一个 200MB 的 exe，服务器 OOM 而不是报错；
  - 状态机漏了 → 前端对着 parsing 无限轮询，用户以为卡死；
  - 回滚漏了 → 删掉资料后向量还在库里，下次检索出一份"列表里没有"的内容；
  - 检索限额漏了 → 用户传一本 300 页攻略，上下文全被它占满。

不联网、不调 embedding、不建真向量库：PDF 抽取和向量写入全部打桩。
数据库用临时文件跑真 SQL（建表、查重、状态更新这些语句本身也要被测）。

跑法：
    cd backend && python -m unittest tests.test_knowledge -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402
from langchain_core.documents import Document  # noqa: E402

from app.api import knowledge as api  # noqa: E402
from app.models.knowledge import KnowledgeDoc, UploadResponse  # noqa: E402
from app.rag import retriever  # noqa: E402
from app.services import knowledge_base as kb  # noqa: E402
from app.services.database import DatabaseService  # noqa: E402

USER = {"id": "u-1", "username": "alice"}
OTHER = {"id": "u-2", "username": "bob"}
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"


def _temp_db() -> DatabaseService:
    """不碰生产库：显式给一个临时 sqlite 路径，跑真的建表语句。"""
    service = DatabaseService.__new__(DatabaseService)
    service.db_path = Path(tempfile.mkdtemp()) / "test.db"
    service._init_db()
    return service


class _UploadFile:
    """UploadFile 的最小替身：接口只用到 read() 和 filename。"""

    def __init__(self, filename, content):
        self.filename = filename
        self._content = content

    async def read(self):
        return self._content


class SanitizeFilenameTest(unittest.TestCase):
    def test_strips_directory_parts(self):
        # 落盘用的是 doc_id，这里只是让展示名别带路径
        self.assertEqual(kb.sanitize_filename("../../etc/passwd.pdf"), "passwd.pdf")
        self.assertEqual(kb.sanitize_filename(r"C:\Users\me\行程.pdf"), "行程.pdf")

    def test_empty_falls_back(self):
        self.assertEqual(kb.sanitize_filename(""), "未命名.pdf")
        self.assertEqual(kb.sanitize_filename("   "), "未命名.pdf")

    def test_truncates_long_name(self):
        self.assertEqual(len(kb.sanitize_filename("攻" * 500 + ".pdf")), 120)


class ValidateUploadTest(unittest.TestCase):
    def test_rejects_empty(self):
        with self.assertRaises(kb.KnowledgeError):
            kb.validate_upload(b"", "a.pdf")

    def test_rejects_oversize(self):
        too_big = PDF_BYTES + b"0" * (kb.MAX_UPLOAD_BYTES + 1)
        with self.assertRaises(kb.KnowledgeError) as ctx:
            kb.validate_upload(too_big, "a.pdf")
        self.assertIn("上限", str(ctx.exception))

    def test_rejects_wrong_extension(self):
        with self.assertRaises(kb.KnowledgeError) as ctx:
            kb.validate_upload(PDF_BYTES, "攻略.docx")
        self.assertIn("只支持 PDF", str(ctx.exception))

    def test_rejects_bad_magic(self):
        # 扩展名对、内容不对：改名的 zip 应该在解析前就被挡住
        with self.assertRaises(kb.KnowledgeError) as ctx:
            kb.validate_upload(b"PK\x03\x04rest", "攻略.pdf")
        self.assertIn("不是 PDF", str(ctx.exception))

    def test_accepts_pdf(self):
        self.assertEqual(kb.validate_upload(PDF_BYTES, "行程.pdf"), "行程.pdf")


class FileHashTest(unittest.TestCase):
    def test_same_content_same_hash(self):
        self.assertEqual(kb.file_hash(PDF_BYTES), kb.file_hash(bytes(PDF_BYTES)))

    def test_different_content_different_hash(self):
        self.assertNotEqual(kb.file_hash(PDF_BYTES), kb.file_hash(PDF_BYTES + b"x"))


class KnowledgeDBTest(unittest.TestCase):
    def setUp(self):
        self.db = _temp_db()
        self.patch = mock.patch.object(kb, "db", self.db)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_create_and_get(self):
        record = self.db.create_knowledge_doc("d1", USER["id"], "a.pdf", "/tmp/a.pdf", 10, "h1")
        self.assertEqual(record["status"], "pending")
        self.assertEqual(self.db.get_knowledge_doc("d1")["filename"], "a.pdf")

    def test_update_only_whitelisted_columns(self):
        self.db.create_knowledge_doc("d1", USER["id"], "a.pdf", "/tmp/a.pdf")
        self.assertTrue(self.db.update_knowledge_doc("d1", status="ready", chunks=7))
        self.assertEqual(self.db.get_knowledge_doc("d1")["chunks"], 7)
        # user_id 不在白名单里：改不动，这是防越权的硬边界
        self.assertFalse(self.db.update_knowledge_doc("d1", user_id="u-2"))

    def test_list_is_per_user(self):
        self.db.create_knowledge_doc("d1", USER["id"], "a.pdf", "/tmp/a.pdf")
        self.db.create_knowledge_doc("d2", OTHER["id"], "b.pdf", "/tmp/b.pdf")
        self.assertEqual([d["doc_id"] for d in self.db.list_knowledge_docs(USER["id"])], ["d1"])

    def test_hash_lookup_ignores_failed(self):
        self.db.create_knowledge_doc("d1", USER["id"], "a.pdf", "/tmp/a.pdf", 1, "h1")
        self.db.update_knowledge_doc("d1", status="failed")
        # 上次失败了，这次重传应该重新入库，不能被那条失败记录挡住
        self.assertIsNone(self.db.find_knowledge_doc_by_hash(USER["id"], "h1"))

    def test_hash_lookup_finds_ready(self):
        self.db.create_knowledge_doc("d1", USER["id"], "a.pdf", "/tmp/a.pdf", 1, "h1")
        self.db.update_knowledge_doc("d1", status="ready")
        self.assertEqual(self.db.find_knowledge_doc_by_hash(USER["id"], "h1")["doc_id"], "d1")

    def test_reset_stale_marks_inflight_as_failed(self):
        # 进程重启后，卡在 parsing 的记录必须被清理，否则前端永远轮询
        self.db.create_knowledge_doc("d1", USER["id"], "a.pdf", "/tmp/a.pdf")
        self.db.update_knowledge_doc("d1", status="indexing")
        self.db.create_knowledge_doc("d2", USER["id"], "b.pdf", "/tmp/b.pdf")
        self.db.update_knowledge_doc("d2", status="ready")

        self.assertEqual(self.db.reset_stale_knowledge_docs(), 1)
        stale = self.db.get_knowledge_doc("d1")
        self.assertEqual(stale["status"], "failed")
        self.assertIn("重启", stale["error"])
        self.assertEqual(self.db.get_knowledge_doc("d2")["status"], "ready")


class SaveUploadTest(unittest.TestCase):
    def setUp(self):
        self.db = _temp_db()
        self.root = Path(tempfile.mkdtemp())
        self.patches = [
            mock.patch.object(kb, "db", self.db),
            mock.patch.object(kb, "upload_dir", lambda user_id: self.root / user_id),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    def test_writes_file_and_registers_record(self):
        record = kb.save_upload(USER["id"], "沈阳.pdf", PDF_BYTES)
        self.assertFalse(record["duplicated"])
        self.assertEqual(record["status"], "pending")
        saved = Path(record["file_path"])
        self.assertTrue(saved.exists())
        # 落盘名是 doc_id，不含用户提供的文件名
        self.assertEqual(saved.name, f"{record['doc_id']}.pdf")
        self.assertEqual(saved.read_bytes(), PDF_BYTES)

    def test_duplicate_returns_existing(self):
        first = kb.save_upload(USER["id"], "沈阳.pdf", PDF_BYTES)
        self.db.update_knowledge_doc(first["doc_id"], status="ready")

        second = kb.save_upload(USER["id"], "沈阳-副本.pdf", PDF_BYTES)
        self.assertTrue(second["duplicated"])
        self.assertEqual(second["doc_id"], first["doc_id"])
        # 只留一份文件，不该为重复上传再写一份
        self.assertEqual(len(list((self.root / USER["id"]).glob("*.pdf"))), 1)

    def test_same_content_different_user_is_not_duplicate(self):
        kb.save_upload(USER["id"], "沈阳.pdf", PDF_BYTES)
        self.db.update_knowledge_doc(self.db.list_knowledge_docs(USER["id"])[0]["doc_id"],
                                     status="ready")
        # 资料是私有资产，B 传同一份文件也要有自己的拷贝
        record = kb.save_upload(OTHER["id"], "沈阳.pdf", PDF_BYTES)
        self.assertFalse(record["duplicated"])


class UploadApiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = _temp_db()
        self.root = Path(tempfile.mkdtemp())
        self.started = []
        self.patches = [
            mock.patch.object(kb, "db", self.db),
            mock.patch.object(kb, "upload_dir", lambda user_id: self.root / user_id),
            # 不真的跑 OCR：这里只验证接口行为，入库本身另有用例
            mock.patch.object(kb, "start_ingest", self.started.append),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    async def test_upload_returns_pending_and_starts_task(self):
        response = await api.upload_pdf(_UploadFile("沈阳.pdf", PDF_BYTES), USER)
        self.assertIsInstance(response, UploadResponse)
        self.assertEqual(response.status, "pending")
        self.assertFalse(response.duplicated)
        self.assertEqual(self.started, [response.doc_id])

    async def test_upload_rejects_bad_file(self):
        with self.assertRaises(HTTPException) as ctx:
            await api.upload_pdf(_UploadFile("假的.pdf", b"not a pdf"), USER)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.started, [])

    async def test_duplicate_upload_does_not_restart_ingest(self):
        first = await api.upload_pdf(_UploadFile("沈阳.pdf", PDF_BYTES), USER)
        self.db.update_knowledge_doc(first.doc_id, status="ready")

        second = await api.upload_pdf(_UploadFile("沈阳.pdf", PDF_BYTES), USER)
        self.assertTrue(second.duplicated)
        self.assertEqual(second.status, "ready")
        # 只启动过一次入库，第二次是直接返回老记录
        self.assertEqual(len(self.started), 1)

    async def test_get_other_users_doc_is_404(self):
        response = await api.upload_pdf(_UploadFile("沈阳.pdf", PDF_BYTES), USER)
        with self.assertRaises(HTTPException) as ctx:
            await api.get_doc(response.doc_id, OTHER)
        # 404 而不是 403：不告诉对方这个 doc_id 存在
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_list_only_returns_own_docs(self):
        await api.upload_pdf(_UploadFile("沈阳.pdf", PDF_BYTES), USER)
        listing = await api.list_docs(USER)
        self.assertEqual(len(listing.docs), 1)
        self.assertIsInstance(listing.docs[0], KnowledgeDoc)
        self.assertEqual((await api.list_docs(OTHER)).docs, [])

    async def test_delete_removes_record_and_file(self):
        response = await api.upload_pdf(_UploadFile("沈阳.pdf", PDF_BYTES), USER)
        record = self.db.get_knowledge_doc(response.doc_id)
        path = Path(record["file_path"])

        with mock.patch.object(kb, "_purge_vectors") as purge:
            result = await api.delete_doc(response.doc_id, USER)

        self.assertTrue(result["success"])
        purge.assert_called_once_with(response.doc_id)
        self.assertFalse(path.exists())
        self.assertIsNone(self.db.get_knowledge_doc(response.doc_id))

    async def test_delete_other_users_doc_is_404_and_keeps_file(self):
        response = await api.upload_pdf(_UploadFile("沈阳.pdf", PDF_BYTES), USER)
        record = self.db.get_knowledge_doc(response.doc_id)

        with self.assertRaises(HTTPException):
            await api.delete_doc(response.doc_id, OTHER)

        self.assertTrue(Path(record["file_path"]).exists())
        self.assertIsNotNone(self.db.get_knowledge_doc(response.doc_id))


class IngestTest(unittest.TestCase):
    """入库状态机 + 失败回滚。抽取与向量写入全部打桩。"""

    PAGES = [{"page": 1, "text": "第一天：沈阳故宫\n上午参观大政殿，下午去中街。"},
             {"page": 2, "text": "第二天：北陵公园\n早上看晨练，中午吃老边饺子。"}]

    def setUp(self):
        self.db = _temp_db()
        self.root = Path(tempfile.mkdtemp())
        self.patches = [
            mock.patch.object(kb, "db", self.db),
            mock.patch.object(kb, "upload_dir", lambda user_id: self.root / user_id),
        ]
        for patch in self.patches:
            patch.start()

        record = kb.save_upload(USER["id"], "沈阳.pdf", PDF_BYTES)
        self.doc_id = record["doc_id"]

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    def _statuses(self):
        return self.db.get_knowledge_doc(self.doc_id)

    def test_success_advances_to_ready(self):
        written = []

        def fake_add(documents, scope="public"):
            written.append((documents, scope))

        with mock.patch.object(kb, "extract_document",
                               return_value={"pages": self.PAGES, "mode": "text"}), \
                mock.patch("app.rag.vector_store.add_documents", fake_add):
            result = kb.ingest_sync(self.doc_id)

        record = self._statuses()
        self.assertEqual(record["status"], "ready")
        self.assertEqual(record["progress"], 100)
        self.assertEqual(record["pages"], 2)
        self.assertEqual(record["chunks"], result["chunks"])
        self.assertEqual(record["extract_mode"], "text")
        # 分块器认出了「第一天/第二天」，这正是「按天切块」要保证的东西
        self.assertEqual(record["days"], 2)
        self.assertTrue(all(scope == "user" for _, scope in written))

        first = written[0][0][0]
        self.assertEqual(first.metadata["doc_id"], self.doc_id)
        self.assertEqual(first.metadata["user_id"], USER["id"])
        self.assertEqual(first.metadata["scope"], "user")

    def test_batches_respect_embedding_batch_size(self):
        # 25 块、批大小 20 → 必须分两次，不能一次全丢给 embedding 接口
        many = [{"page": i, "text": f"第{i}天：景点{i}\n" + "游览内容。" * 30}
                for i in range(1, 26)]
        calls = []

        def fake_add(documents, scope="public"):
            calls.append(len(documents))

        with mock.patch.object(kb, "extract_document",
                               return_value={"pages": many, "mode": "text"}), \
                mock.patch("app.rag.vector_store.add_documents", fake_add), \
                mock.patch.object(kb.settings, "embedding_batch_size", 20):
            kb.ingest_sync(self.doc_id)

        self.assertGreaterEqual(sum(calls), 1)
        self.assertTrue(all(size <= 20 for size in calls))

    def test_extract_failure_marks_failed(self):
        with mock.patch.object(kb, "extract_document",
                               side_effect=kb.PdfNoTextError("没有文字层")):
            with self.assertRaises(kb.KnowledgeError):
                kb.ingest_sync(self.doc_id)
        record = self._statuses()
        self.assertEqual(record["status"], "failed")
        self.assertIn("没有文字层", record["error"])

    def test_empty_chunks_marks_failed(self):
        # 抽取成功但一行有效文字都没有（纯图片页）：不能当成功入库一个空文档
        with mock.patch.object(kb, "extract_document",
                               return_value={"pages": [{"page": 1, "text": ""}],
                                             "mode": "text"}):
            with self.assertRaises(kb.KnowledgeError):
                kb.ingest_sync(self.doc_id)
        self.assertEqual(self._statuses()["status"], "failed")

    def test_embedding_failure_rolls_back_written_chunks(self):
        many = [{"page": i, "text": f"第{i}天：景点{i}\n" + "游览内容。" * 30}
                for i in range(1, 41)]
        calls = {"n": 0}
        purged = []

        def flaky_add(documents, scope="public"):
            calls["n"] += 1
            if calls["n"] >= 2:          # 第一批成功，第二批炸
                raise RuntimeError("embedding 503")

        with mock.patch.object(kb, "extract_document",
                               return_value={"pages": many, "mode": "text"}), \
                mock.patch("app.rag.vector_store.add_documents", flaky_add), \
                mock.patch.object(kb.settings, "embedding_batch_size", 5), \
                mock.patch.object(kb, "_purge_vectors", purged.append):
            with self.assertRaises(kb.KnowledgeError):
                kb.ingest_sync(self.doc_id)

        record = self._statuses()
        self.assertEqual(record["status"], "failed")
        self.assertIn("向量化失败", record["error"])
        # 已经写进去的那批必须清掉，否则留下检索得到、列表里没有的孤儿块
        self.assertEqual(purged, [self.doc_id])

    def test_ingest_swallows_unexpected_exceptions(self):
        # 后台任务没有调用方在等结果，任何异常都必须落进状态而不是冒到事件循环
        import asyncio

        with mock.patch.object(kb, "extract_document", side_effect=RuntimeError("磁盘满")):
            self.assertIsNone(asyncio.run(kb.ingest(self.doc_id)))

        record = self._statuses()
        self.assertEqual(record["status"], "failed")
        self.assertIn("磁盘满", record["error"])


class RetrievalMergeTest(unittest.TestCase):
    """合并逻辑：排序、去重、单份资料限额。都不联网。"""

    @staticmethod
    def _doc(text, doc_id="a.pdf", scope="public", page=1):
        return Document(page_content=text,
                        metadata={"doc_id": doc_id, "scope": scope,
                                  "source": doc_id, "page_start": page})

    def test_orders_by_distance(self):
        candidates = [(self._doc("远"), 0.9), (self._doc("近"), 0.1), (self._doc("中"), 0.5)]
        picked = retriever._merge(candidates, top_k=3, max_per_doc=5)
        self.assertEqual([d.page_content for d in picked], ["近", "中", "远"])

    def test_respects_top_k(self):
        candidates = [(self._doc(f"块{i}"), i / 10) for i in range(10)]
        self.assertEqual(len(retriever._merge(candidates, top_k=3, max_per_doc=10)), 3)

    def test_single_document_still_fills_top_k(self):
        # 回归：公共语料目前只有一份攻略，候选全来自它。限额如果写死 2，
        # top_k=5 会被压成 2 条 —— 等于凭空砍掉一半上下文。
        candidates = [(self._doc(f"攻略第{i}段", doc_id="only.pdf"), i / 100) for i in range(6)]
        self.assertEqual(len(retriever._merge(candidates, top_k=5, max_per_doc=2)), 5)

    def test_caps_dominant_document_when_alternatives_exist(self):
        # 有五份以上资料可选时，单份最多 2 块，防止一本大书占满上下文
        candidates = [(self._doc(f"大书第{i}段", doc_id="big.pdf"), i / 100) for i in range(6)]
        for index in range(5):
            candidates.append((self._doc(f"资料{index}", doc_id=f"s{index}.pdf"), 0.8))
        picked = retriever._merge(candidates, top_k=5, max_per_doc=2)

        self.assertEqual(len(picked), 5)
        self.assertEqual(sum(1 for d in picked if d.metadata["doc_id"] == "big.pdf"), 2)

    def test_two_documents_split_slots(self):
        # 两份资料：各 3 条上限。小资料只有 1 条候选，剩下空位由兜底轮次
        # 用大书的次优块补齐 —— 大书不会一边倒占满，但也别浪费上下文位置。
        candidates = [(self._doc(f"大书第{i}段", doc_id="big.pdf"), i / 100) for i in range(6)]
        candidates.append((self._doc("另一份资料", doc_id="small.pdf"), 0.5))
        picked = retriever._merge(candidates, top_k=5, max_per_doc=2)

        self.assertEqual(len(picked), 5)
        self.assertEqual(sum(1 for d in picked if d.metadata["doc_id"] == "small.pdf"), 1)

    def test_dedupes_identical_content(self):
        candidates = [(self._doc("同一段", doc_id="a.pdf"), 0.1),
                      (self._doc("同一段", doc_id="b.pdf"), 0.2)]
        self.assertEqual(len(retriever._merge(candidates, top_k=5, max_per_doc=5)), 1)


class FormatDocsTest(unittest.TestCase):
    def test_labels_user_docs_distinctly(self):
        # 模型要能区分「内置攻略」和「用户自己传的」，否则会把用户的计划
        # 当第三方资料复述
        doc = Document(page_content="第一天去故宫",
                       metadata={"scope": "user", "filename": "我的沈阳行程.pdf",
                                 "page_start": 3, "day_label": "第一天"})
        text = retriever.format_retrieved_docs([doc])
        self.assertIn("你上传的资料《我的沈阳行程.pdf》", text)
        self.assertIn("第3页", text)

    def test_public_doc_uses_source(self):
        doc = Document(page_content="大连攻略", metadata={"source": "我是驴友-大连.md"})
        text = retriever.format_retrieved_docs([doc])
        self.assertIn("我是驴友-大连.md", text)
        self.assertNotIn("你上传的", text)

    def test_empty_returns_placeholder(self):
        self.assertIn("没有找到", retriever.format_retrieved_docs([]))


if __name__ == "__main__":
    unittest.main()
