# app/services/database.py
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict

from app.config import settings


class DatabaseService:
    """数据库服务 - 管理会话和消息"""

    def __init__(self):
        self.db_path = Path(settings.checkpoint_db_path)
        self._init_db()

    def _get_connection(self):
        """获取数据库连接"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """初始化数据库表"""
        # 确保目录存在
        db_dir = self.db_path.parent
        if not db_dir.exists():
            db_dir.mkdir(parents=True, exist_ok=True)
            print(f"✅ 创建数据库目录: {db_dir}")
            conn = self._get_connection()
            cursor = conn.cursor()

            # 1. 创建会话表
            cursor.execute("""CREATE TABLE IF NOT EXISTS sessions
                              (
                                  thread_id
                                  TEXT
                                  PRIMARY
                                  KEY,
                                  user_id
                                  TEXT
                                  NOT
                                  NULL,
                                  name
                                  TEXT
                                  NOT
                                  NULL,
                                  created_at
                                  TEXT
                                  NOT
                                  NULL,
                                  updated_at
                                  TEXT
                                  NOT
                                  NULL
                              )""")

            # 2. 创建消息表
            cursor.execute("""CREATE TABLE IF NOT EXISTS messages
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                map_data TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES sessions(thread_id) ON DELETE CASCADE)""")

            cursor.execute("PRAGMA table_info(messages)")
            columns = [row[1] for row in cursor.fetchall()]
            if "map_data" not in columns:
                cursor.execute("ALTER TABLE messages ADD COLUMN map_data TEXT")
                print("✅ 已自动添加 map_data 字段")

            # 3. 创建索引（提高查询性能）
            cursor.execute("""
                           CREATE INDEX IF NOT EXISTS idx_messages_thread_id
                               ON messages(thread_id)
                           """)
            cursor.execute("""
                           CREATE INDEX IF NOT EXISTS idx_messages_timestamp
                               ON messages(timestamp)
                           """)

            conn.commit()
            conn.close()
        print("✅ 数据库初始化完成")

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
            print("会话创建成功")
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
        print("会话名称更新成功")
        return affected > 0

    def delete_session(self, thread_id: str) -> bool:
        """删除会话（消息会自动级联删除）"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM sessions WHERE thread_id = ?", (thread_id,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        print("会话删除成功")
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
        获取会话的所有消息

        Args:
            thread_id: 会话ID
            limit: 返回最近的消息数量（默认50条）
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
                       SELECT *
                       FROM messages
                       WHERE thread_id = ?
                       ORDER BY timestamp ASC
                           LIMIT ?
                       """, (thread_id, limit))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

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


db = DatabaseService()
