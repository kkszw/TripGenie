// frontend/src/composables/useSessions.js
// 模块级状态：App.vue 与 ChatView.vue 都在用它，之前每次都 new 一份 ref，
// 结果是两份互不相干的 sessions 数组——ChatView 里刷新的是一份没人渲染的副本，
// 后端改过会话名后侧边栏要等别的操作才更新。这里改成单例，只保留一份真相。
import {ref} from 'vue'
import {api} from '@/utils/api'

const sessions = ref([])
const currentThreadId = ref(null)
const currentSession = ref(null)
const isLoading = ref(false)
const error = ref(null)

// ===== 加载会话列表 =====
const loadSessions = async () => {
    isLoading.value = true
    error.value = null
    try {
        const data = await api.getSessions()
        sessions.value = data.map(s => ({
            thread_id: s.thread_id || s.id,       // 后端用 id，前端用 thread_id
            name: s.name || s.title || '新对话',   // 后端用 title，前端用 name
            created_at: s.created_at,
            updated_at: s.updated_at,
            message_count: s.message_count
        }))
    } catch (err) {
        error.value = err.message
        console.error('❌ 加载会话失败:', err)
    } finally {
        isLoading.value = false
    }
}

// ===== 清空本地状态（退出登录 / 换账号时调用）=====
const resetSessions = () => {
    sessions.value = []
    currentThreadId.value = null
    currentSession.value = null
    error.value = null
}

// ===== 创建新会话 =====
const createSession = async (name = '新对话') => {
    try {
        const newSession = await api.createSession(name)
        sessions.value.unshift(newSession)
        currentThreadId.value = newSession.thread_id
        currentSession.value = newSession
        return newSession
    } catch (err) {
        console.error('❌ 创建会话失败:', err)
        throw err
    }
}

// ===== 删除会话 =====
const deleteSession = async (threadId) => {
    try {
        await api.deleteSession(threadId)
        sessions.value = sessions.value.filter(s => s.thread_id !== threadId)
        if (currentThreadId.value === threadId) {
            currentThreadId.value = null
            currentSession.value = null
        }
        return true
    } catch (err) {
        console.error('❌ 删除会话失败:', err)
        return false
    }
}

// ===== 选择会话 =====
const selectSession = (threadId) => {
    const session = sessions.value.find(s => s.thread_id === threadId)
    if (session) {
        currentThreadId.value = threadId
        currentSession.value = session
        return session
    }
    return null
}

// ===== 更新会话名称 =====
const updateSessionTitle = async (threadId, name) => {
    try {
        await api.updateSessionTitle(threadId, name)

        const session = sessions.value.find(s => s.thread_id === threadId)
        if (session) {
            session.name = name
        }
        if (currentSession.value && currentSession.value.thread_id === threadId) {
            currentSession.value.name = name
        }
        sessions.value = [...sessions.value]
        return true
    } catch (err) {
        console.error('❌ 更新标题失败:', err)
        return false
    }
}

// ===== 获取会话消息 =====
const loadSessionMessages = async (threadId) => {
    try {
        return await api.getSessionMessages(threadId)
    } catch (err) {
        console.error('❌ 加载消息失败:', err)
        return []
    }
}

export function useSessions() {
    return {
        sessions,
        currentThreadId,
        currentSession,
        isLoading,
        error,
        loadSessions,
        resetSessions,
        createSession,
        deleteSession,
        selectSession,
        updateSessionTitle,
        loadSessionMessages
    }
}
