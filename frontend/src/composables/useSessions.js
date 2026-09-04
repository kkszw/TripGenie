// frontend/src/composables/useSessions.js
import {ref} from 'vue'
import {api} from '@/utils/api'

export function useSessions() {
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
            thread_id: s.thread_id || s.id,              // 后端用 id，前端用 thread_id
            name: s.name || s.title || '新对话',    // ✅ 后端用 title，前端用 name
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

    // ===== 创建新会话 =====
    const createSession = async (name = '新对话') => {
        try {
            const newSession = await api.createSession(name)
            sessions.value.unshift(newSession)
            currentThreadId.value = newSession.thread_id
            currentSession.value = newSession
            console.log('🆕 创建会话:', newSession.thread_id)
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
            console.log('🗑️ 删除会话:', threadId)
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
            // 1. 调用 API 更新数据库
            await api.updateSessionTitle(threadId, name)

            // 2. ✅ 更新本地 sessions 列表
            const session = sessions.value.find(s => s.thread_id === threadId)
            if (session) {
                session.name = name
                console.log('✅ 本地会话名称已更新:', threadId, name)
            }

            // 3. ✅ 如果是当前会话，也更新 currentSession
            if (currentSession.value && currentSession.value.thread_id === threadId) {
                currentSession.value.name = name
            }

            // 4. ✅ 强制更新响应式（Vue 3 会自动检测，但为了保险）
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
            const messages = await api.getSessionMessages(threadId)
            return messages
        } catch (err) {
            console.error('❌ 加载消息失败:', err)
            return []
        }
    }

    return {
        sessions,
        currentThreadId,
        currentSession,
        isLoading,
        error,
        loadSessions,
        createSession,
        deleteSession,
        selectSession,
        updateSessionTitle,
        loadSessionMessages
    }
}