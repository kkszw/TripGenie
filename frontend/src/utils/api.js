// frontend/src/utils/api.js
import {getToken, clearToken} from './token'

const API_BASE = '';
const BIZ_TYPE = 'trip-assistant';

// 统一注入令牌
const authHeaders = (extra = {}) => {
    const token = getToken();
    return token ? {...extra, Authorization: `Bearer ${token}`} : {...extra};
};

// 后端 detail 可能是字符串，也可能是 pydantic 的校验错误数组
const toError = async (response, fallback) => {
    let detail = '';
    try {
        const data = await response.json();
        detail = data.detail || data.message || '';
        if (Array.isArray(detail)) {
            detail = detail.map(d => d.msg || JSON.stringify(d)).join('；');
        }
    } catch (e) {
        // 响应不是 JSON，忽略
    }
    return new Error(detail || `${fallback}（HTTP ${response.status}）`);
};

// 401 意味着令牌失效：清掉本地令牌并广播事件，由 useAuth 弹出登录窗
const onUnauthorized = (response) => {
    if (response.status === 401) {
        clearToken();
        window.dispatchEvent(new CustomEvent('auth:expired'));
    }
};

export const api = {
    // ===== 认证 =====

    // 登录：故意不做 401 处理，密码错了不该触发"登录过期"
    login: async function (username, password) {
        const response = await fetch(`${API_BASE}/api/auth/login`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username, password})
        });
        if (!response.ok) throw await toError(response, '登录失败');
        return response.json();
    },

    register: async function (username, password) {
        const response = await fetch(`${API_BASE}/api/auth/register`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username, password})
        });
        if (!response.ok) throw await toError(response, '注册失败');
        return response.json();
    },

    // 校验本地令牌是否仍有效，顺便拿回用户信息
    me: async function () {
        const response = await fetch(`${API_BASE}/api/auth/me`, {
            headers: authHeaders()
        });
        if (!response.ok) {
            onUnauthorized(response);
            throw await toError(response, '获取用户信息失败');
        }
        return response.json();
    },

    // ===== 会话管理 =====

    // 获取当前用户的会话列表（user_id 由令牌决定，不再作为参数传递）
    getSessions: async function () {
        const response = await fetch(`${API_BASE}/api/history/`, {
            headers: authHeaders()
        });
        if (!response.ok) {
            onUnauthorized(response);
            throw await toError(response, '获取会话列表失败');
        }
        const data = await response.json();
        const sessions = Array.isArray(data) ? data : (data.sessions || []);

        // 后端返回 id，前端统一用 thread_id
        return sessions.map(s => ({
            ...s,
            thread_id: s.thread_id || s.id || s.session_id
        }));
    },

    // 创建新会话
    createSession: async function (name = '新对话') {
        const response = await fetch(`${API_BASE}/api/history/sessions`, {
            method: 'POST',
            headers: authHeaders({'Content-Type': 'application/json'}),
            body: JSON.stringify({
                biz_type: BIZ_TYPE,
                name: name
            })
        });

        if (!response.ok) {
            onUnauthorized(response);
            throw await toError(response, '创建会话失败');
        }

        return response.json();
    },

    // 删除会话
    deleteSession: async function (threadId) {
        const response = await fetch(`${API_BASE}/api/history/${threadId}`, {
            method: 'DELETE',
            headers: authHeaders()
        });

        if (!response.ok) {
            onUnauthorized(response);
            throw await toError(response, '删除会话失败');
        }

        return response.json();
    },

    // 更新会话名称
    updateSessionTitle: async function (threadId, name) {
        const response = await fetch(`${API_BASE}/api/history/sessions/${threadId}`, {
            method: 'PUT',
            headers: authHeaders({'Content-Type': 'application/json'}),
            body: JSON.stringify({title: name})
        });

        if (!response.ok) {
            onUnauthorized(response);
            throw await toError(response, '更新标题失败');
        }
        return response.json();
    },

    // ===== 消息管理 =====

    // 获取会话消息
    getSessionMessages: async function (threadId) {
        const response = await fetch(`${API_BASE}/api/history/${threadId}`, {
            headers: authHeaders()
        });

        if (!response.ok) {
            onUnauthorized(response);
            throw await toError(response, '获取消息失败');
        }

        const data = await response.json();
        return data.messages || [];
    },

    // 发送消息（流式）
    // options.signal：AbortSignal，用于「停止生成」和切换会话时掐断请求。
    // 不传 signal 时行为与原来完全一致，fetch 默认没有超时/取消。
    sendMessage: async function (threadId, message, options = {}) {
        const body = {
            thread_id: threadId,
            message: message,
            context: {}
        };

        const response = await fetch(`${API_BASE}/api/chat/stream`, {
            method: 'POST',
            headers: authHeaders({'Content-Type': 'application/json'}),
            body: JSON.stringify(body),
            signal: options.signal
        });

        if (!response.ok) {
            onUnauthorized(response);
            throw await toError(response, '发送消息失败');
        }

        return response;
    },

    // HITL：把确认卡上的决定送回图里，从 checkpoint 原地恢复
    // payload 形如 {action:'confirm'} / {action:'cancel'} / {action:'edit', days:5}
    resumeChat: async function (threadId, payload, options = {}) {
        const response = await fetch(`${API_BASE}/api/chat/resume`, {
            method: 'POST',
            headers: authHeaders({'Content-Type': 'application/json'}),
            body: JSON.stringify({thread_id: threadId, payload}),
            signal: options.signal
        });
        if (!response.ok) {
            onUnauthorized(response);
            throw await toError(response, '确认失败');
        }
        return response;
    },

    // 会话里有没有待确认的中断。刷新页面 / 切回会话后靠它把确认卡找回来。
    // 查询失败不该打断界面，所以这里吞掉异常返回 null。
    getPending: async function (threadId) {
        try {
            const response = await fetch(`${API_BASE}/api/chat/pending/${threadId}`, {
                headers: authHeaders()
            });
            if (!response.ok) return {pending: null};
            return await response.json();
        } catch (e) {
            return {pending: null};
        }
    }
};

// 导出常量
export {BIZ_TYPE};
