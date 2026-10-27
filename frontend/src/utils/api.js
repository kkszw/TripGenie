// frontend/src/utils/api.js
const API_BASE = '';
const USER_ID = 'default';
const BIZ_TYPE = 'trip-assistant';

export const api = {
    // ===== 会话管理 =====

    // 获取所有会话
    getSessions: async function () {
        const response = await fetch(
            `${API_BASE}/api/history?user_id=${USER_ID}`
        );
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '获取会话列表失败');
        }
        const data = await response.json();
        // return Array.isArray(data) ? data : (data.sessions || []);
        const sessions = Array.isArray(data) ? data : (data.sessions || []);

        // ✅ 如果后端返回的是 id，转换为 thread_id
        return sessions.map(s => ({
            ...s,
            thread_id: s.thread_id || s.id || s.session_id  // 兼容多种字段名
        }));
    },

    // 创建新会话
    createSession: async function (name = '新对话') {
        const response = await fetch(`${API_BASE}/api/history/sessions`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                user_id: USER_ID,
                biz_type: BIZ_TYPE,
                name: name
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '创建会话失败');
        }

        return response.json();
    },

    // 删除会话
    deleteSession: async function (threadId) {
        const response = await fetch(`${API_BASE}/api/history/${threadId}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '删除会话失败');
        }

        return response.json();
    },

    // 更新会话名称
    updateSessionTitle: async function (threadId, name) {
        const response = await fetch(`${API_BASE}/api/history/sessions/${threadId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                thread_id: threadId,
                user_id: USER_ID,
                title:name})
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '更新标题失败');
        }
        console.log("会话标题更新成功")
        return response.json();
    },

    // ===== 消息管理 =====

    // 获取会话消息
    getSessionMessages: async function (threadId) {
        const response = await fetch(
            `${API_BASE}/api/history/${threadId}`
        );

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '获取消息失败');
        }

        const data = await response.json();
        return data.messages || [];
    },

    // ✅ 发送消息（流式）- 这个就是 sendMessage
    sendMessage: async function (threadId, message) {
        const body = {
            thread_id: threadId,
            message: message,
            context: {}
        };

        const response = await fetch(`${API_BASE}/api/chat/stream`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '发送消息失败');
        }

        return response;
    }
};

// 导出常量
export {USER_ID, BIZ_TYPE};