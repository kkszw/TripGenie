# app/services/database.py
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict

from app.config import settings
from app.core.logging import get_logger
from app.utils.path_tool import get_absolute_path
# estimate_tokens 已挪到 utils/token_tool.py：检索侧也要用它设上下文预算，
# 而本模块末尾有 db 单例，import 即建库。这里重新导出，旧的 import 路径继续可用。
from app.utils.token_tool import estimate_tokens  # noqa: F401

logger = get_logger(__name__)


# ==================== 上下文长度控制 ====================
# 对话历史按 token 预算截断，不是按条数。估算规则见 utils/token_tool.py。

def trim_to_token_budget(messages: List[Dict], max_tokens: int) -> List[Dict]:
    """从最新一条往前收，直到预算用完，返回时间正序的那一段。

    两条边界规则：

    1. **至少留一条**。单条就超预算时（例如一整份行程 JSON）也收下——
       空历史比"只有一句超长回复"更糟，模型会完全不知道上一轮说过什么；
    2. **补回被截断的提问**。截断点可能正好落在一问一答之间，只留一条
       assistant 回复会让模型看到"没有问题的答案"，所以把它前面的 user
       一起带回来。

    max_tokens <= 0 表示不限制（与 MAP_POLYLINE_TOLERANCE_M=0 关闭抽稀
    的约定一致）。
    """
    if not messages:
        return []
    if max_tokens <= 0:
        return list(messages)

    picked: List[Dict] = []
    used = 0
    for msg in reversed(messages):
        cost = estimate_tokens(msg.get("content") or "")
        if picked and used + cost > max_tokens:
            break
        picked.append(msg)
        used += cost

    picked.reverse()

    # picked 一定是 messages 的连续后缀，所以起点用长度差算，不靠 index 查值
    # （两条内容完全相同的消息会让 index 找错位置）。
    if picked and picked[0].get("role") == "assistant":
        start = len(messages) - len(picked)
        if start > 0:
            picked.insert(0, messages[start - 1])

    return picked



class DatabaseService:
    """数据库服务 - 管理会话和消息"""

    def __init__(self, db_path: Optional[str] = None):
        # 路径相对 app/ 解析，与启动目录无关。
        # db_path 只给测试用：分页这类"顺序 + 边界"的逻辑必须能在临时库上跑，
        # 不能让它去碰真实的 sessions.db。
        if db_path:
            self.db_path = Path(db_path)
        else:
            self.db_path = Path(get_absolute_path(settings.checkpoint_db_path))
        self._init_db()

    def _get_connection(self):
        """获取数据库连接"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        # SQLite 默认不启用外键约束，必须每个连接显式打开，
        # 否则 messages 表的 ON DELETE CASCADE 不会生效，删会话会留下孤儿消息
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        """初始化数据库表"""
        # 确保目录存在
        db_dir = self.db_path.parent
        if not db_dir.exists():
            db_dir.mkdir(parents=True, exist_ok=True)
            logger.debug("创建数据库目录", extra={"path": str(db_dir)})

        # 建表必须无条件执行。CREATE TABLE IF NOT EXISTS 本身是幂等的，而一旦把建表
        # 塞进上面的 if 里，"目录存在但表不存在"的情况（只删了 sessions.db、或 clone
        # 后留下空目录）就永远不会建表，之后每次请求都报 no such table: sessions，
        # 日志却照样打印"初始化完成"。
        conn = self._get_connection()
        cursor = conn.cursor()

        # 0. 用户表（登录系统）
        # COLLATE NOCASE：Alice 与 alice 视为同一个用户名（唯一约束也按不区分大小写生效）
        cursor.execute("""CREATE TABLE IF NOT EXISTS users
                          (
                              id            TEXT PRIMARY KEY,
                              username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
                              password_hash TEXT NOT NULL,
                              created_at    TEXT NOT NULL
                          )""")
        # username 已 UNIQUE，但 SQLite 的 UNIQUE 区分大小写；这里额外建索引便于查询
        cursor.execute("""
                       CREATE INDEX IF NOT EXISTS idx_users_username
                           ON users (username)
                       """)

        # 1. 创建会话表
        cursor.execute("""CREATE TABLE IF NOT EXISTS sessions
                          (
                              thread_id  TEXT PRIMARY KEY,
                              user_id    TEXT NOT NULL,
                              name       TEXT NOT NULL,
                              created_at TEXT NOT NULL,
                              updated_at TEXT NOT NULL
                          )""")

        # 2. 创建消息表
        cursor.execute("""CREATE TABLE IF NOT EXISTS messages
                          (
                              id        INTEGER PRIMARY KEY AUTOINCREMENT,
                              thread_id TEXT NOT NULL,
                              role      TEXT NOT NULL,
                              content   TEXT NOT NULL,
                              map_data  TEXT,
                              timestamp TEXT NOT NULL,
                              FOREIGN KEY (thread_id) REFERENCES sessions (thread_id) ON DELETE CASCADE
                          )""")

        # 老库可能没有 map_data 列，补上
        cursor.execute("PRAGMA table_info(messages)")
        columns = [row[1] for row in cursor.fetchall()]
        if "map_data" not in columns:
            cursor.execute("ALTER TABLE messages ADD COLUMN map_data TEXT")
            logger.info("已自动为 messages 表添加 map_data 字段")

        # 3. 用户上传的资料（PDF 知识库）
        # 为什么单独一张表而不是扩 sessions：资料是跨会话复用的资产，用户传一份
        # 「我的沈阳行程」，之后任何一次对话都该能检索到它，挂在会话上就绑死了。
        #
        # status 是显式的状态机而不是「有没有 chunks」来推断：上传是异步的，
        # 前端要轮询进度，光看 chunks=0 分不清「正在解析」和「解析失败」。
        cursor.execute("""CREATE TABLE IF NOT EXISTS knowledge_docs
                          (
                              doc_id       TEXT PRIMARY KEY,
                              user_id      TEXT NOT NULL,
                              filename     TEXT NOT NULL,
                              file_path    TEXT NOT NULL,
                              size_bytes   INTEGER NOT NULL DEFAULT 0,
                              file_hash    TEXT,
                              status       TEXT NOT NULL,
                              progress     INTEGER NOT NULL DEFAULT 0,
                              pages        INTEGER NOT NULL DEFAULT 0,
                              chunks       INTEGER NOT NULL DEFAULT 0,
                              chars        INTEGER NOT NULL DEFAULT 0,
                              days         INTEGER NOT NULL DEFAULT 0,
                              extract_mode TEXT,
                              error        TEXT,
                              created_at   TEXT NOT NULL,
                              updated_at   TEXT NOT NULL
                          )""")
        cursor.execute("""
                       CREATE INDEX IF NOT EXISTS idx_knowledge_user
                           ON knowledge_docs (user_id, created_at)
                       """)
        # 同一用户的同一份文件只入库一次：靠 (user_id, file_hash) 查重。
        # 不加 UNIQUE 约束——重复上传应该返回"已存在"并把老记录给前端，而不是报错。
        cursor.execute("""
                       CREATE INDEX IF NOT EXISTS idx_knowledge_hash
                           ON knowledge_docs (user_id, file_hash)
                       """)

        # 4. 创建索引（提高查询性能）
        cursor.execute("""
                       CREATE INDEX IF NOT EXISTS idx_messages_thread_id
                           ON messages (thread_id)
                       """)
        cursor.execute("""
                       CREATE INDEX IF NOT EXISTS idx_messages_timestamp
                           ON messages (timestamp)
                       """)
        # 按用户查会话是登录后的高频路径，会话表也要有 user_id 索引
        cursor.execute("""
                       CREATE INDEX IF NOT EXISTS idx_sessions_user_id
                           ON sessions (user_id)
                       """)

        # 5. 用户长期档案（跨会话记忆）
        # 为什么不从 sessions/messages 现算：那两张表记的是"发生过什么"，这张表
        # 记的是"由此得出的结论"。现算意味着每轮扫全部历史，而且结论随上下文
        # 摇摆——同一句「不吃辣」在不同会话里抽出来的措辞不一样，注入进 prompt
        # 的字节就不一样，缓存前缀跟着失效。
        #
        # 主键用 (user_id, key) 而不是自增 id：key 是固定枚举
        # （见 core/prompts/memory.py 的 MEMORY_KEYS），一个用户一个键只该有一个值，
        # 改主意就是覆盖。允许一个键多行的话「不吃辣」和「能吃辣了」会并存，
        # 注入时自相矛盾，模型只能二选一瞎猜。
        cursor.execute("""CREATE TABLE IF NOT EXISTS user_memory
                          (
                              user_id          TEXT NOT NULL,
                              key              TEXT NOT NULL,
                              category         TEXT NOT NULL,
                              value            TEXT NOT NULL,
                              source           TEXT,
                              source_thread_id TEXT,
                              created_at       TEXT NOT NULL,
                              updated_at       TEXT NOT NULL,
                              PRIMARY KEY (user_id, key)
                          )""")

        conn.commit()
        conn.close()
        logger.info("数据库初始化完成", extra={"path": str(self.db_path)})

    # ==================== 用户操作 ====================

    def create_user(self, user_id: str, username: str, password_hash: str) -> Optional[Dict]:
        """
        创建用户。用户名已存在时返回 None（由上层转成 400）。
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        try:
            cursor.execute("""
                           INSERT INTO users (id, username, password_hash, created_at)
                           VALUES (?, ?, ?, ?)
                           """, (user_id, username, password_hash, now))
            conn.commit()
            return {
                "id": user_id,
                "username": username,
                "created_at": now,
            }
        except sqlite3.IntegrityError:
            return None
        finally:
            conn.close()

    def get_user_by_username(self, username: str) -> Optional[Dict]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> Optional[Dict]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        # 去掉哈希，避免被顺手带进日志或响应
        if row:
            data = dict(row)
            data.pop("password_hash", None)
            return data
        return None

    # ==================== 会话操作 ====================

    def create_session(self, thread_id: str, user_id: str = "default", name: str = "新对话") -> Dict:
        """创建新会话"""
        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.now().isoformat()

        try:
            cursor.execute("""
                           INSERT INTO sessions (thread_id, user_id, name, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?)
                           """, (thread_id, user_id, name, now, now))
            conn.commit()
            logger.debug("会话已创建", extra={"thread_id": thread_id, "user_id": user_id})
            return {
                "thread_id": thread_id,
                "user_id": user_id,
                "name": name,
                "created_at": now,
                "updated_at": now
            }
        except sqlite3.IntegrityError:
            # 会话已存在
            return {}
        finally:
            conn.close()

    def get_session(self, thread_id: str) -> Optional[Dict]:
        """获取单个会话信息"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM sessions WHERE thread_id = ?", (thread_id,))
        row = cursor.fetchone()
        conn.close()

        return dict(row) if row else None

    def get_all_sessions(self, user_id: str = "default") -> List[Dict]:
        """获取用户的所有会话"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
                       SELECT s.*, COUNT(m.id) as message_count
                       FROM sessions s
                                LEFT JOIN messages m ON s.thread_id = m.thread_id
                       WHERE s.user_id = ?
                       GROUP BY s.thread_id
                       ORDER BY s.updated_at DESC
                       """, (user_id,))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def update_session_name(self, thread_id: str, name: str) -> bool:
        """更新会话名称"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
                       UPDATE sessions
                       SET name       = ?,
                           updated_at = ?
                       WHERE thread_id = ?
                       """, (name, datetime.now().isoformat(), thread_id))

        affected = cursor.rowcount
        conn.commit()
        conn.close()
        logger.debug("会话名称已更新", extra={"thread_id": thread_id})
        return affected > 0

    def delete_session(self, thread_id: str) -> bool:
        """删除会话（消息会自动级联删除）"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM sessions WHERE thread_id = ?", (thread_id,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        logger.debug("会话已删除", extra={"thread_id": thread_id})
        return affected > 0

    # ==================== 消息操作 ====================

    def add_message(self, thread_id: str, role: str, content: str, map_data: dict = None) -> bool:
        """
        添加消息到会话

        Args:
            thread_id: 会话ID
            role: 'user' 或 'assistant'
            content: 消息内容
            map_data: 地图数据（可选，只有旅行规划消息才有）
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        map_data_json = json.dumps(map_data, ensure_ascii=False) if map_data else None

        # 1. 插入消息
        cursor.execute("""
                       INSERT INTO messages (thread_id, role, content, timestamp, map_data)
                       VALUES (?, ?, ?, ?, ?)
                       """, (thread_id, role, content, datetime.now().isoformat(), map_data_json))

        # 2. 更新会话的 updated_at
        cursor.execute("""
                       UPDATE sessions
                       SET updated_at = ?
                       WHERE thread_id = ?
                       """, (datetime.now().isoformat(), thread_id))

        conn.commit()
        conn.close()

        return True

    def add_messages_batch(self, thread_id: str, messages: List[Dict]) -> bool:
        """
        批量添加消息

        Args:
            thread_id: 会话ID
            messages: [{"role": "user", "content": "..."}, ...]
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        now = datetime.now().isoformat()

        # 批量插入消息
        for msg in messages:
            cursor.execute("""
                           INSERT INTO messages (thread_id, role, content, timestamp)
                           VALUES (?, ?, ?, ?)
                           """, (thread_id, msg["role"], msg["content"], now))

        # 更新会话时间
        cursor.execute("""
                       UPDATE sessions
                       SET updated_at = ?
                       WHERE thread_id = ?
                       """, (now, thread_id))

        conn.commit()
        conn.close()

        return True

    def get_messages(self, thread_id: str, limit: int = 50) -> List[Dict]:
        """
        获取会话最近的 limit 条消息（按时间正序返回）

        Args:
            thread_id: 会话ID
            limit: 返回最近的消息数量（默认50条）
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 先倒序取"最近"的 limit 条，外层再正序排回来。
        # 原来的 ORDER BY timestamp ASC LIMIT ? 取的是最旧的 50 条，长会话里
        # 模型永远看不到最近说的话（"刚才那个行程改一下"会直接失忆）。
        # 加 id 兜底：add_messages_batch 一批消息共用同一个 timestamp，只按时间排会乱序。
        cursor.execute("""
                       SELECT *
                       FROM (SELECT *
                             FROM messages
                             WHERE thread_id = ?
                             ORDER BY timestamp DESC, id DESC
                             LIMIT ?)
                       ORDER BY timestamp ASC, id ASC
                       """, (thread_id, limit))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_messages_page(self, thread_id: str, limit: int = 50,
                          before_id: Optional[int] = None) -> Dict:
        """按游标取一页消息，返回时间正序的一段。

        为什么用 id 游标而不是 offset：
        - 聊天记录是只增的，翻页期间新消息只会追加在尾部，用 `id < before`
          翻旧页不会错位；OFFSET 一旦有新消息进来，后一页会重复或漏掉。
        - id 是自增主键，就是插入顺序，add_messages_batch 一批消息共用同一个
          timestamp 时它也是唯一的先后依据（get_messages 里的排序同理）。

        Args:
            thread_id: 会话 ID
            limit: 这一页最多几条（调用方负责上下限，这里只做个兜底）
            before_id: 取 id 小于它的消息；None 表示取最新的一页

        Returns:
            {"messages": [...时间正序...], "has_more": 是否还有更早的}
        """
        limit = max(1, int(limit))
        conn = self._get_connection()
        cursor = conn.cursor()

        # 多取一条用来判断"还有没有更早的"，比再查一次 COUNT(*) 便宜，
        # 也避免两次查询之间数据变化导致 has_more 和实际页不一致。
        if before_id is None:
            cursor.execute("""
                           SELECT * FROM messages
                           WHERE thread_id = ?
                           ORDER BY id DESC
                           LIMIT ?
                           """, (thread_id, limit + 1))
        else:
            cursor.execute("""
                           SELECT * FROM messages
                           WHERE thread_id = ? AND id < ?
                           ORDER BY id DESC
                           LIMIT ?
                           """, (thread_id, int(before_id), limit + 1))

        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()

        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()  # 倒序取的，翻回时间正序给前端

        return {"messages": rows, "has_more": has_more}

    def get_context_messages(self, thread_id: str, max_tokens: int = 6000,
                             scan_limit: int = 500) -> List[Dict]:
        """取注入模型的历史消息：最近的一段，按 token 预算截断（时间正序）。

        为什么不再用「最近 50 条」：条数和成本无关。50 条短问答可能只有几百
        token，50 条带行程 JSON 的对话能到几万——单次请求的输入成本差两个数量
        级，上下文窗口也可能被顶爆。

        scan_limit 只是内存保护：长会话可能有上千条消息，先按条数粗筛一遍，
        再在候选里按 token 精确截。500 条 × 平均 100~200 token，足够挑出
        几千 token 的预算，不必把整张表读进内存。
        """
        recent = self.get_messages(thread_id, limit=scan_limit)
        return trim_to_token_budget(recent, max_tokens)

    def get_last_messages(self, thread_id: str, limit: int = 10) -> List[Dict]:
        """获取最近的 N 条消息（用于上下文）"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
                       SELECT *
                       FROM messages
                       WHERE thread_id = ?
                       ORDER BY timestamp DESC
                           LIMIT ?
                       """, (thread_id, limit))

        rows = cursor.fetchall()
        conn.close()

        # 反转顺序（从旧到新）
        return [dict(row) for row in reversed(rows)]

    def get_chat_history(self, thread_id: str) -> List[Dict]:
        """
        获取完整对话历史（格式化为 LangChain 消息格式）
        """
        messages = self.get_messages(thread_id)

        history = []
        for msg in messages:
            history.append({
                "role": msg["role"],
                "content": msg["content"]
            })

        return history

    def delete_messages(self, thread_id: str) -> bool:
        """删除会话的所有消息"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()

        return affected > 0


    # ==================== 用户长期档案（跨会话记忆） ====================

    def get_user_memory(self, user_id: str) -> List[Dict]:
        """取一个用户的长期档案。

        排序必须是确定的，而且这里就把优先级定下来：硬约束在前、偏好在后，
        同类按 key 字典序。这条消息会插在 system 之后当缓存前缀，用 updated_at
        排序的话，一条记忆被 touch 一下顺序就变，同一用户下一轮的前缀就和上
        一轮对不上——缓存全部失效，输入价差 50 倍。
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
                       SELECT *
                       FROM user_memory
                       WHERE user_id = ?
                       ORDER BY CASE category WHEN 'constraint' THEN 0 ELSE 1 END, key
                       """, (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def upsert_user_memory(self, user_id: str, key: str, value: str,
                           category: str = "preference", source: str = "chat",
                           source_thread_id: Optional[str] = None) -> bool:
        """写入或覆盖一条记忆。返回「值真的变了」才算 True。

        为什么带 `WHERE user_memory.value <> excluded.value`：抽取是每轮都可能跑
        的后台任务，同一句「不吃辣」会被反复抽出来。不加这个条件，每条记忆的
        updated_at 每轮都在跳，等于一直在告诉后来读它的人"这条刚更新过"，
        真出问题时反而分不清哪些是最近改的。
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute("""
                       INSERT INTO user_memory
                       (user_id, key, category, value, source, source_thread_id,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (user_id, key) DO UPDATE SET
                           value            = excluded.value,
                           category         = excluded.category,
                           source           = excluded.source,
                           source_thread_id = excluded.source_thread_id,
                           updated_at       = excluded.updated_at
                       WHERE user_memory.value <> excluded.value
                       """, (user_id, key, category, value, source, source_thread_id,
                             now, now))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    def delete_user_memory(self, user_id: str, key: str) -> bool:
        """删掉一条记忆。用户改主意（"我现在能吃辣了"）走这里。"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_memory WHERE user_id = ? AND key = ?",
                       (user_id, key))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    def clear_user_memory(self, user_id: str) -> int:
        """清空一个用户的全部记忆，返回删掉的条数。"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_memory WHERE user_id = ?", (user_id,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected

    # ==================== 用户资料（PDF 知识库） ====================

    def create_knowledge_doc(self, doc_id: str, user_id: str, filename: str,
                             file_path: str, size_bytes: int = 0,
                             file_hash: str = "") -> Dict:
        """登记一份待入库的资料。状态从 pending 开始，由后台任务推进。"""
        conn = self._get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute("""
                       INSERT INTO knowledge_docs
                       (doc_id, user_id, filename, file_path, size_bytes, file_hash,
                        status, progress, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                       """, (doc_id, user_id, filename, file_path, size_bytes, file_hash,
                             now, now))
        conn.commit()
        conn.close()
        return {
            "doc_id": doc_id, "user_id": user_id, "filename": filename,
            "file_path": file_path, "size_bytes": size_bytes, "file_hash": file_hash,
            "status": "pending", "progress": 0, "pages": 0, "chunks": 0,
            "chars": 0, "days": 0, "extract_mode": None, "error": None,
            "created_at": now, "updated_at": now,
        }

    def update_knowledge_doc(self, doc_id: str, **fields) -> bool:
        """更新资料状态。只允许更新白名单里的列，避免调用方拼关键字注入。"""
        allowed = {"status", "progress", "pages", "chunks", "chars", "days",
                   "extract_mode", "error", "file_hash"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        conn = self._get_connection()
        cursor = conn.cursor()
        # 列名来自白名单常量集，值全部走占位符，不存在注入面
        assignments = ", ".join(f"{key} = ?" for key in updates)
        cursor.execute(
            f"UPDATE knowledge_docs SET {assignments}, updated_at = ? WHERE doc_id = ?",
            (*updates.values(), datetime.now().isoformat(), doc_id))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    def get_knowledge_doc(self, doc_id: str) -> Optional[Dict]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM knowledge_docs WHERE doc_id = ?", (doc_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def list_knowledge_docs(self, user_id: str) -> List[Dict]:
        """列出某个用户的资料，最新的在前。"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
                       SELECT *
                       FROM knowledge_docs
                       WHERE user_id = ?
                       ORDER BY created_at DESC
                       """, (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def find_knowledge_doc_by_hash(self, user_id: str, file_hash: str) -> Optional[Dict]:
        """按内容哈希查重。返回该用户之前上传过的同一份文件（若有）。"""
        if not file_hash:
            return None
        conn = self._get_connection()
        cursor = conn.cursor()
        # 只认已成功的：上次失败的那条不该挡住这次重试
        cursor.execute("""
                       SELECT *
                       FROM knowledge_docs
                       WHERE user_id = ? AND file_hash = ? AND status = 'ready'
                       ORDER BY created_at DESC
                       LIMIT 1
                       """, (user_id, file_hash))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def delete_knowledge_doc(self, doc_id: str) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM knowledge_docs WHERE doc_id = ?", (doc_id,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    def reset_stale_knowledge_docs(self) -> int:
        """把重启前没跑完的入库任务标记为失败。

        后台任务活在进程内存里，进程一重启就没了，但数据库里那条记录还停在
        parsing/indexing。不清理的话，前端会一直转圈等一个永远不会来的结果。
        返回被清理的条数。
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
                       UPDATE knowledge_docs
                       SET status = 'failed',
                           error  = '服务重启，入库任务被中断，请重新上传',
                           updated_at = ?
                       WHERE status IN ('pending', 'parsing', 'chunking', 'indexing')
                       """, (datetime.now().isoformat(),))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        if affected:
            logger.warning("清理僵尸入库任务", extra={"count": affected})
        return affected


db = DatabaseService()
