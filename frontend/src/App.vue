<!-- frontend/src/App.vue -->
<template>
  <div id="app" class="app-container">
    <!-- 侧边栏 -->
    <aside class="sidebar" :class="{ collapsed: isSidebarCollapsed }">
      <div class="sidebar-header">
        <div class="logo" @click="goHome">
          <span class="logo-icon">🧳</span>
          <span class="logo-text" v-show="!isSidebarCollapsed">TripGenie</span>
        </div>
        <button class="collapse-btn" @click="toggleSidebar">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M15 18L9 12L15 6" stroke-linecap="round"/>
          </svg>
        </button>
      </div>

      <!-- 新建对话按钮 -->
      <button class="new-chat-btn" @click="createNewChat">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M12 5V19M5 12H19" stroke-linecap="round"/>
        </svg>
        <span v-show="!isSidebarCollapsed">新建对话</span>
      </button>

      <!-- 对话历史列表 -->
      <div class="chat-history">
        <div v-if="isLoadingSessions" class="loading-sessions">
          <span>加载中...</span>
        </div>

        <div v-else-if="sessions.length === 0" class="empty-history">
          <span>暂无对话记录</span>
        </div>

        <div
            v-for="session in sessions"
            :key="session.thread_id"
            class="chat-item"
            :class="{ active: session.thread_id && session.thread_id === currentThreadId }"
            @click="switchChat(session.thread_id)"
        >
          <span class="chat-icon">💬</span>
          <span class="chat-title" v-show="!isSidebarCollapsed">
            {{ session.name || '新对话' }}
          </span>
          <span class="chat-time" v-show="!isSidebarCollapsed">
            {{ formatTime(session.created_at) }}
          </span>

          <div class="chat-actions" v-show="!isSidebarCollapsed">
            <div class="dropdown-wrapper">
              <button
                  class="chat-action-btn more-btn"
                  @click.stop="toggleDropdown(session.thread_id)"
                  title="更多操作"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <circle cx="12" cy="5" r="2"/>
                  <circle cx="12" cy="12" r="2"/>
                  <circle cx="12" cy="19" r="2"/>
                </svg>
              </button>

              <div v-if="activeDropdownId === session.thread_id" class="dropdown-menu" @click.stop>
                <button class="dropdown-item" @click="renameChat(session)">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path
                        d="M11 4H4C3.46957 4 2.96086 4.21071 2.58579 4.58579C2.21071 4.96086 2 5.46957 2 6V20C2 20.5304 2.21071 21.0391 2.58579 21.4142C2.96086 21.7893 3.46957 22 4 22H18C18.5304 22 19.0391 21.7893 19.4142 21.4142C19.7893 21.0391 20 20.5304 20 20V13"
                        stroke-linecap="round"/>
                    <path d="M18.5 2.5L21.5 5.5L12 15H9V12L18.5 2.5Z" stroke-linecap="round"/>
                  </svg>
                  重命名
                </button>
                <button class="dropdown-item danger" @click="handleDelete(session.thread_id)">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path
                        d="M3 6H21M19 6V20C19 21.1046 18.1046 22 17 22H7C5.89543 22 5 21.1046 5 20V6M8 6V4C8 3.44772 8.44772 3 9 3H15C15.5523 3 16 3.44772 16 4V6"
                        stroke-linecap="round"/>
                    <path d="M10 11V17M14 11V17" stroke-linecap="round"/>
                  </svg>
                  删除
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="sidebar-footer">
        <button class="footer-btn" @click="openSettings">
          <span v-show="!isSidebarCollapsed">设置</span>
        </button>
      </div>
    </aside>

    <!-- 主聊天区域 -->
    <main class="main-content">
      <ChatView
          ref="chatViewRef"
          :key="currentThreadId"
          :thread-id="currentThreadId"
          @new-chat="createNewChat"
          @update-title="updateSessionTitle"
          @toggle-sidebar="toggleSidebar"
          @thread-created="onThreadCreated"
      />
    </main>
  </div>
</template>

<script setup>
import {ref, onMounted} from 'vue'
import ChatView from './views/ChatView.vue'
import {useSessions} from '@/composables/useSessions'

const isSidebarCollapsed = ref(false)
const activeDropdownId = ref(null)
const chatViewRef = ref(null)

const {
  sessions,
  currentThreadId,
  isLoading: isLoadingSessions,
  loadSessions,
  createSession,
  deleteSession,
  selectSession,
  updateSessionTitle
} = useSessions()

// ===== 侧边栏 =====
const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value
}

// ===== 新建对话 =====
const createNewChat = async () => {
  try {
    const session = await createSession('新对话')
    currentThreadId.value = session.thread_id
    chatViewRef.value?.clearMessages()
    chatViewRef.value?.focusInput()
  } catch (error) {
    console.error('创建会话失败:', error)
  }
  await loadSessions()
}

// ===== 切换对话 =====
const switchChat = async (threadId) => {
  if (threadId === currentThreadId.value) {
    return
  }
  const session = selectSession(threadId)
  if (session) {
    currentThreadId.value = threadId
    // console.log('✅ 切换到会话:', session.name)
    await chatViewRef.value?.loadSessionMessages(threadId)
  }
}

// ===== 删除对话 =====
const handleDelete = async (threadId) => {
  if (!confirm('确定要删除这个对话吗？')) return

  const success = await deleteSession(threadId)
  if (success) {
    activeDropdownId.value = null
    if (currentThreadId.value === threadId) {
      currentThreadId.value = null
      chatViewRef.value?.clearMessages()
    }
  }
  await loadSessions()
}

// ===== 重命名对话 =====
const renameChat = async (session) => {
  const newName = prompt('请输入新的对话名称:', session.name || '新对话')
  if (newName && newName.trim()) {
    try {
      // ✅ 等待更新完成
      const success = await updateSessionTitle(session.thread_id, newName.trim())
      if (success) {
        console.log('✅ 会话重命名成功')
        // ✅ 重新加载会话列表
        await loadSessions()
        console.log('✅ 会话列表已刷新')
      } else {
        console.error('❌ 重命名失败')
        alert('重命名失败，请重试')
      }
    } catch (error) {
      console.error('❌ 重命名错误:', error)
      alert('重命名失败：' + error.message)
    }
    // 关闭下拉菜单
    activeDropdownId.value = null
  }
}

// ===== 新线程创建回调 =====
const onThreadCreated = async (threadId) => {
  console.log('🆕 线程创建:', threadId)
  await loadSessions()  // 刷新会话列表
}

// ===== 下拉菜单 =====
const toggleDropdown = (id) => {
  activeDropdownId.value = activeDropdownId.value === id ? null : id
}

// ===== 格式化时间 =====
const formatTime = (dateStr) => {
  if (!dateStr) return '刚刚'
  const date = new Date(dateStr)
  const now = new Date()
  const diff = now - date

  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`
  if (diff < 604800000) return `${Math.floor(diff / 86400000)}天前`
  return date.toLocaleDateString()
}

// ===== 其他 =====
const goHome = () => {
  createNewChat()
}

const openSettings = () => {
  alert('设置功能开发中...')
}

// ===== 初始化 =====
onMounted(() => {
  loadSessions()

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.dropdown-wrapper')) {
      activeDropdownId.value = null
    }
  })
})
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  background: #f7f8fa;
  color: #1a1a1a;
  height: 100vh;
  overflow: hidden;
}

#app {
  height: 100vh;
}

.app-container {
  display: flex;
  height: 100vh;
  background: #ffffff;
}

/* ===== 侧边栏 ===== */
.sidebar {
  width: 260px;
  min-width: 260px;
  height: 100vh;
  background: #f7f8fa;
  border-right: 1px solid #e5e7eb;
  display: flex;
  flex-direction: column;
  transition: all 0.3s ease;
  overflow: hidden;
  position: relative;
  z-index: 10;
}

.sidebar.collapsed {
  width: 56px;
  min-width: 56px;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 16px 12px;
  flex-shrink: 0;
}

.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-weight: 600;
  font-size: 18px;
  color: #1a1a1a;
  user-select: none;
}

.logo-icon {
  font-size: 24px;
}

.logo-text {
  white-space: nowrap;
}

.collapse-btn {
  background: none;
  border: none;
  padding: 4px;
  cursor: pointer;
  color: #9ca3af;
  border-radius: 6px;
  transition: all 0.2s ease;
}

.collapse-btn:hover {
  background: #e5e7eb;
  color: #1a1a1a;
}

.sidebar.collapsed .collapse-btn svg {
  transform: rotate(180deg);
}

/* 新建对话按钮 */
.new-chat-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 12px 12px;
  padding: 10px 14px;
  background: #4f6ef7;
  color: white;
  border: none;
  border-radius: 10px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.2s ease;
  flex-shrink: 0;
}

.new-chat-btn:hover {
  background: #3b5de7;
  transform: scale(1.02);
}

.sidebar.collapsed .new-chat-btn {
  padding: 10px;
  justify-content: center;
}

/* 对话历史列表 */
.chat-history {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
}

.chat-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s ease;
  color: #6b7280;
  font-size: 14px;
  position: relative;
  min-height: 40px;
}

.chat-item:hover {
  background: #e5e7eb;
  color: #1a1a1a;
}

.chat-item:hover .chat-actions {
  opacity: 1;
}

.chat-item.active {
  background: #eef2ff;
  color: #4f6ef7;
}

.chat-item .chat-icon {
  flex-shrink: 0;
}

.chat-item .chat-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-item .chat-time {
  font-size: 11px;
  color: #9ca3af;
  flex-shrink: 0;
  margin-right: 4px;
}

/* 加载状态 */
.loading-sessions {
  padding: 20px;
  text-align: center;
  color: #9ca3af;
  font-size: 13px;
}

.empty-history {
  padding: 40px 20px;
  text-align: center;
  color: #9ca3af;
  font-size: 13px;
}

/* 三个点菜单 */
.chat-actions {
  display: flex;
  align-items: center;
  opacity: 0;
  transition: opacity 0.2s ease;
  flex-shrink: 0;
}

.chat-item:hover .chat-actions {
  opacity: 1;
}

.dropdown-wrapper {
  position: relative;
}

.chat-action-btn {
  background: none;
  border: none;
  padding: 4px 6px;
  cursor: pointer;
  color: #9ca3af;
  border-radius: 4px;
  transition: all 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.chat-action-btn:hover {
  background: #d1d5db;
  color: #1a1a1a;
}

.more-btn {
  padding: 4px 6px !important;
}

.more-btn:hover {
  background: #d1d5db !important;
}

.dropdown-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 4px);
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
  padding: 4px;
  min-width: 140px;
  z-index: 50;
  animation: dropdownFadeIn 0.15s ease;
}

@keyframes dropdownFadeIn {
  from {
    opacity: 0;
    transform: translateY(-4px) scale(0.96);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.dropdown-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 12px;
  background: none;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: #1a1a1a;
  transition: all 0.15s ease;
}

.dropdown-item:hover {
  background: #f7f8fa;
}

.dropdown-item.danger {
  color: #ef4444;
}

.dropdown-item.danger:hover {
  background: #fee2e2;
}

.sidebar.collapsed .chat-item .chat-title,
.sidebar.collapsed .chat-item .chat-time,
.sidebar.collapsed .chat-item .chat-actions {
  display: none;
}

.sidebar.collapsed .chat-item {
  justify-content: center;
  padding: 10px;
}

/* 侧边栏底部 */
.sidebar-footer {
  padding: 12px 8px;
  border-top: 1px solid #e5e7eb;
  flex-shrink: 0;
}

.footer-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 12px;
  background: none;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  color: #6b7280;
  font-size: 14px;
  transition: all 0.15s ease;
}

.footer-btn:hover {
  background: #e5e7eb;
  color: #1a1a1a;
}

.sidebar.collapsed .footer-btn {
  justify-content: center;
  padding: 8px;
}

.sidebar.collapsed .footer-btn span {
  display: none;
}

/* ===== 主内容 ===== */
.main-content {
  flex: 1;
  height: 100vh;
  overflow: hidden;
  background: #ffffff;
  position: relative;
}

/* ===== 滚动条 ===== */
::-webkit-scrollbar {
  width: 4px;
  height: 4px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: #e5e7eb;
  border-radius: 2px;
}

::-webkit-scrollbar-thumb:hover {
  background: #9ca3af;
}

/* ===== 响应式 ===== */
@media (max-width: 768px) {
  .sidebar {
    position: fixed;
    left: 0;
    top: 0;
    z-index: 100;
    transform: translateX(0);
    box-shadow: 2px 0 12px rgba(0, 0, 0, 0.1);
  }

  .sidebar.collapsed {
    transform: translateX(-100%);
  }

  .main-content {
    width: 100%;
  }
}
</style>