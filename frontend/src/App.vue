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
        <button class="collapse-btn" @click="toggleSidebar" title="收起边栏">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9">
            <rect x="3" y="4" width="18" height="16" rx="3"/>
            <path d="M9 4V20"/>
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
          <!-- 用提问的前两个字做缩略标记，配色按 thread_id 固定，方便一眼区分 -->
          <span
              class="chat-badge"
              :style="{ backgroundColor: sessionTone(session).bg, color: sessionTone(session).fg }"
          >{{ sessionBadge(session) }}</span>
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
        <!-- 已登录：显示用户名 + 退出 -->
        <div v-if="isLoggedIn" class="user-box">
          <div class="user-avatar">{{ avatarLetter }}</div>
          <div class="user-meta" v-show="!isSidebarCollapsed">
            <span class="user-name" :title="user.username">{{ user.username }}</span>
            <button class="logout-btn" @click="handleLogout">退出登录</button>
          </div>
        </div>
        <!-- 未登录：给一个重新打开的入口 -->
        <button v-else class="footer-btn" @click="openLogin">
          <span v-show="!isSidebarCollapsed">登录 / 注册</span>
        </button>
      </div>
    </aside>

    <!-- 主聊天区域 -->
    <main class="main-content">
      <ChatView
          ref="chatViewRef"
          :key="currentThreadId"
          :thread-id="currentThreadId"
          :sidebar-collapsed="isSidebarCollapsed"
          @new-chat="createNewChat"
          @update-title="updateSessionTitle"
          @toggle-sidebar="toggleSidebar"
          @thread-created="onThreadCreated"
          @require-login="openLogin"
      />
    </main>

    <!-- 登录窗（未登录时强制显示，不提供关闭） -->
    <LoginModal @success="onLoginSuccess" />

    <!-- 全局确认 / 输入 / 提示弹窗（替代浏览器原生弹窗） -->
    <AppDialog />
  </div>
</template>

<script setup>
import {computed, onMounted, ref, watch} from 'vue'
import ChatView from './views/ChatView.vue'
import LoginModal from '@/components/auth/LoginModal.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import {useSessions} from '@/composables/useSessions'
import {useAuth} from '@/composables/useAuth'
import {alertDialog, confirmDialog, promptDialog} from '@/composables/useDialog'

const isSidebarCollapsed = ref(false)
const activeDropdownId = ref(null)
const chatViewRef = ref(null)

const {
  sessions,
  currentThreadId,
  isLoading: isLoadingSessions,
  loadSessions,
  resetSessions,
  createSession,
  deleteSession,
  selectSession,
  updateSessionTitle
} = useSessions()

const {user, isLoggedIn, initAuth, logout, openLogin} = useAuth()

const avatarLetter = computed(() => (user.value?.username || '?').slice(0, 1).toUpperCase())

// ===== 会话缩略标记 =====
// 每个会话的图标改成「提问的前两个字」，颜色按 thread_id 哈希固定，同一会话永远同一个色
const BADGE_TONES = [
  {bg: '#eef2ff', fg: '#4f6ef7'},
  {bg: '#e7f7f1', fg: '#0f9d76'},
  {bg: '#fff3e6', fg: '#d97706'},
  {bg: '#fdeef5', fg: '#db2777'},
  {bg: '#e9f4ff', fg: '#0284c7'},
  {bg: '#f3efff', fg: '#7c3aed'}
]

const sessionTone = (session) => {
  const key = String(session?.thread_id || session?.name || 'x')
  let hash = 0
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0
  return BADGE_TONES[hash % BADGE_TONES.length]
}

const sessionBadge = (session) => {
  // 去掉空白和标点，避免把「嗯，」「好的\n」这类噪声字符当标题
  const text = (session?.name || '').replace(/[\s"'“”‘’「」《》（）()：:,，.。!！?？、]/g, '')
  if (!text || text === '新对话') return '新'
  // 英文 / 数字开头的取两个字母，中文取前两个字
  if (/^[A-Za-z0-9]/.test(text)) return text.slice(0, 2).toUpperCase()
  return text.slice(0, 2)
}

// ===== 侧边栏 =====
const toggleSidebar = () => {
  isSidebarCollapsed.value = !isSidebarCollapsed.value
}

// ===== 新建对话 =====
const createNewChat = async () => {
  if (!isLoggedIn.value) {
    openLogin()
    return
  }
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
  const target = sessions.value.find(s => s.thread_id === threadId)
  const name = target?.name || '新对话'

  const confirmed = await confirmDialog({
    title: '删除这个对话？',
    message: `「${name}」里的聊天记录会被永久删除，无法恢复。`,
    confirmText: '删除',
    cancelText: '再想想',
    variant: 'danger',
    icon: 'trash'
  })
  if (!confirmed) return

  const success = await deleteSession(threadId)
  if (success) {
    activeDropdownId.value = null
    if (currentThreadId.value === threadId) {
      currentThreadId.value = null
      chatViewRef.value?.clearMessages()
    }
  } else {
    await alertDialog({
      title: '删除失败',
      message: '服务器没有确认删除，请检查网络后重试。',
      variant: 'danger',
      icon: 'warning'
    })
  }
  await loadSessions()
}

// ===== 重命名对话 =====
const renameChat = async (session) => {
  const newName = await promptDialog({
    title: '重命名对话',
    message: '换一个更容易找到的名字。',
    inputLabel: '对话名称',
    placeholder: '例如：三亚五日亲子游',
    value: session.name || '新对话',
    maxLength: 30,
    confirmText: '保存',
    icon: 'edit'
  })
  activeDropdownId.value = null
  if (newName === null) return            // 取消
  if (newName === (session.name || '').trim()) return  // 没改动，不用请求

  try {
    const success = await updateSessionTitle(session.thread_id, newName)
    if (success) {
      await loadSessions()
    } else {
      await alertDialog({
        title: '重命名失败',
        message: '修改没有保存成功，请稍后重试。',
        variant: 'danger',
        icon: 'warning'
      })
    }
  } catch (error) {
    console.error('❌ 重命名错误:', error)
    await alertDialog({
      title: '重命名失败',
      message: error?.message || '请求出错了，请稍后重试。',
      variant: 'danger',
      icon: 'warning'
    })
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

// ===== 登录 / 退出 =====
// 登录成功后：清掉上一位用户残留的会话与消息，再拉自己的历史
const onLoginSuccess = async () => {
  currentThreadId.value = null
  activeDropdownId.value = null
  chatViewRef.value?.clearMessages()
  await loadSessions()
}

const handleLogout = async () => {
  const confirmed = await confirmDialog({
    title: '退出登录？',
    message: '退出后需要重新登录才能继续聊天，你的对话记录会保留在账号里。',
    confirmText: '退出登录',
    cancelText: '留在页面',
    variant: 'danger',
    icon: 'logout'
  })
  if (!confirmed) return

  logout()            // 内部会清令牌并弹出登录窗
  resetSessions()     // 本地会话列表必须清空，否则下一位用户会看到上一位的标题
  currentThreadId.value = null
  chatViewRef.value?.clearMessages()
}

// 登录态变化时同步会话列表
// 注意只处理"退出"这一侧：登录后的加载由 onLoginSuccess 负责，
// 两边都写会导致登录瞬间并发拉两次列表。
watch(isLoggedIn, (loggedIn) => {
  if (!loggedIn) resetSessions()
})

// ===== 初始化 =====
onMounted(async () => {
  // 先校验本地令牌：有效则直接进聊天页，无效则自动弹登录窗
  await initAuth()
  if (isLoggedIn.value) {
    await loadSessions()
  }

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
  /* 收起后彻底隐藏：只留左上角一个悬浮按钮负责展开，不再保留 56px 窄条 */
  display: none;
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
  flex-shrink: 0;
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

/* 新建对话按钮 */
.new-chat-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 12px 12px;
  padding: 10px 14px;
  background: linear-gradient(135deg, #5b78ff, #4f6ef7);
  color: white;
  border: none;
  border-radius: 10px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  box-shadow: 0 6px 16px rgba(79, 110, 247, 0.24);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
  flex-shrink: 0;
}

.new-chat-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 22px rgba(79, 110, 247, 0.3);
}

.new-chat-btn:active {
  transform: translateY(0);
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
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
  color: #6b7280;
  font-size: 14px;
  position: relative;
  min-height: 40px;
}

.chat-item:hover {
  background: #eceef3;
  color: #1a1a1a;
}

.chat-item:hover .chat-actions {
  opacity: 1;
}

.chat-item.active {
  background: #eef2ff;
  color: #4f6ef7;
  font-weight: 500;
}

.chat-item .chat-badge {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
  letter-spacing: -0.3px;
  user-select: none;
}

.chat-item.active .chat-badge {
  box-shadow: 0 0 0 2px #ffffff, 0 0 0 3px rgba(79, 110, 247, 0.25);
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

/* 侧边栏底部 */
.sidebar-footer {
  padding: 12px 8px;
  border-top: 1px solid #eef0f4;
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

/* 底部用户信息 */
.user-box {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 10px;
  transition: background 0.15s ease;
}

.user-box:hover {
  background: #eceef3;
}

.user-avatar {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 9px;
  background: linear-gradient(135deg, #5b78ff, #7c3aed);
  color: #ffffff;
  font-size: 12px;
  font-weight: 600;
  user-select: none;
  box-shadow: 0 3px 10px rgba(79, 110, 247, 0.24);
}

.user-meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.user-name {
  font-size: 13px;
  color: #1a1a1a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logout-btn {
  align-self: flex-start;
  padding: 0;
  border: none;
  background: none;
  font-size: 11.5px;
  color: #9ca3af;
  cursor: pointer;
  transition: color 0.15s ease;
}

.logout-btn:hover {
  color: #ef4444;
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
    display: none;
  }

  .main-content {
    width: 100%;
  }
}
</style>