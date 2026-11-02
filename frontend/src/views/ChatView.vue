<!-- frontend/src/views/ChatView.vue -->
<template>
  <!-- 有缩略表时给内容区让出右侧空间，避免气泡被压住 -->
  <div class="chat-container" :class="{ 'with-rail': showRail }">
    <!-- 顶部标题栏 -->
    <header class="chat-header" :class="{ 'is-collapsed': sidebarCollapsed }">
      <div class="header-left">
        <!-- 侧边栏收起后：左上角悬浮控制条（logo + 胶囊按钮组） -->
        <div v-if="sidebarCollapsed" class="float-bar">
          <span class="fb-logo">🧳</span>
          <div class="fb-pill">
            <button
                class="fb-btn is-primary"
                data-tip="打开边栏"
                @click="$emit('toggle-sidebar')"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9">
                <rect x="3" y="4" width="18" height="16" rx="3"/>
                <path d="M9 4V20"/>
              </svg>
            </button>
            <button
                class="fb-btn"
                data-tip="新建对话"
                @click="$emit('new-chat')"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9">
                <circle cx="12" cy="12" r="9"/>
                <path d="M12 8.5V15.5M8.5 12H15.5" stroke-linecap="round"/>
              </svg>
            </button>
          </div>
        </div>

        <button v-show="!sidebarCollapsed" class="menu-btn" @click="$emit('toggle-sidebar')" title="切换侧边栏">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 12H21M3 6H21M3 18H21" stroke-linecap="round"/>
          </svg>
        </button>
        <span class="chat-title">{{ currentTitle }}</span>
      </div>
      <!-- 收起后「新建对话」在悬浮条里，右上角的重复入口先藏起来 -->
      <div v-show="!sidebarCollapsed" class="header-right">
        <button class="header-btn" @click="$emit('new-chat')" title="新建对话 (Ctrl+N)">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 5V19M5 12H19" stroke-linecap="round"/>
          </svg>
        </button>
      </div>
    </header>

    <!-- 消息列表 -->
    <div ref="messagesWrapper" class="messages-wrapper">
      <div class="messages-container">
        <!-- 欢迎界面 -->
        <div v-if="messages.length === 0 && !isLoading" class="welcome-screen">
          <div class="welcome-icon">🧳</div>
          <h1 class="welcome-title">开始你的智能旅行</h1>
          <p class="welcome-subtitle">
            告诉我你的目的地和偏好，我将为你规划完美行程
          </p>
          <div class="quick-examples">
            <button
                v-for="example in quickExamples"
                :key="example.label"
                class="example-btn"
                @click="handleExample(example.text)"
            >
              <span class="example-icon">{{ example.icon }}</span>
              {{ example.label }}
            </button>
          </div>
        </div>

        <!-- 消息列表 -->
        <div v-for="(msg, index) in messages" :key="index" class="message-wrapper">
          <!-- AI 消息 -->
          <div v-if="msg.role === 'assistant'" class="message-ai fade-in-up">
            <div class="avatar avatar-ai">🧳</div>
            <div class="message-content">
              <div class="message-bubble bubble-ai" :class="{ 'bubble-plain': !!msg.tripPlan }">
                <!-- ✅ 紧凑的思考过程 -->
                <div v-if="msg.thinkingSteps && msg.thinkingSteps.length > 0" class="thinking-chain">
                  <div class="thinking-chain-header" @click="toggleThinking(index)">
                    <span class="chain-title">思考过程</span>
                    <span class="chain-status">
                <span v-if="msg.isThinking" class="status-running">● 思考中</span>
                <span v-else class="status-done">✓ 已完成</span>
              </span>
                    <span class="chain-toggle">{{ msg.showThinking ? '−' : '+' }}</span>
                  </div>

                  <div v-if="msg.showThinking" class="thinking-chain-body">
                    <div
                        v-for="(step, stepIndex) in msg.thinkingSteps"
                        :key="stepIndex"
                        class="chain-step"
                        :class="step.status"
                    >
                      <!-- 连接线 -->
                      <div class="step-connector">
                        <div class="connector-line" :class="{ active: stepIndex < msg.thinkingSteps.length - 1 }"></div>
                        <div class="connector-dot" :class="step.status"></div>
                      </div>

                      <div class="step-info">
                        <span class="step-name">{{ step.node }}</span>
                        <span class="step-status-text">
                    <span v-if="step.status === 'running'">⏳</span>
                    <span v-else-if="step.status === 'done'">✅</span>
                    <span v-else-if="step.status === 'error'">❌</span>
                  </span>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- ✅ 如果有行程卡片，隐藏原始文本，只显示卡片 -->
                <div v-if="msg.tripPlan" class="trip-plan-wrapper">
                  <TripPlanCard
                      :plan="msg.tripPlan"
                      @expand="showPlanDetail"
                  />
                </div>

                <!-- ✅ 没有行程卡片时才显示原始文本 -->
                <div v-else class="message-text" style="white-space: pre-wrap;">{{ msg.content }}</div>
              </div>
            </div>
          </div>

          <!-- 用户消息：气泡靠右，宽度随内容收缩；data-turn 供右侧缩略表定位 -->
          <div v-else class="message-user fade-in-up" :data-turn="index">
            <div class="message-content">
              <!-- 用 v-text 而非插值：气泡是 pre-wrap，插值会把模板缩进也渲染出来 -->
              <div class="message-bubble bubble-user" v-text="msg.content"></div>
              <div class="message-time">
                {{ formatTime(msg.timestamp) }}
              </div>
            </div>
            <div class="avatar avatar-user">{{ userLetter }}</div>
          </div>
        </div>

        <!-- 加载指示器 -->
        <div v-if="isLoading" class="message-ai fade-in-up">
          <div class="avatar avatar-ai">🧳</div>
          <div class="message-content">
            <div class="message-bubble bubble-ai">
              <div class="typing-indicator">
                <span></span>
                <span></span>
                <span></span>
              </div>
            </div>
          </div>
        </div>

        <TripPlanModal
            v-if="selectedPlan"
            v-model:visible="showModal"
            :plan="selectedPlan"
            @close="closeModal"
        />

        <div ref="bottomRef" style="height: 1px;"></div>
      </div>
    </div>

    <!-- 输入框居中布局 -->
    <div class="input-wrapper">
      <div class="input-container">
        <div class="input-inner">
          <textarea
              ref="inputTextarea"
              v-model="inputText"
              class="chat-input"
              :placeholder="isLoading ? 'AI 正在思考...' : '描述你的旅行计划...'"
              :disabled="isLoading"
              rows="1"
              @keydown.enter.exact.prevent="handleSend"
              @input="autoResize"
          ></textarea>

          <!-- 底部工具栏 -->
          <div class="input-toolbar">
            <div class="toolbar-left">
              <!-- 这里可以放快捷按钮 -->
            </div>
            <div class="toolbar-right">
              <button
                  class="send-btn"
                  :class="{ active: inputText.trim() }"
                  @click="handleSend"
                  :disabled="!inputText.trim() || isLoading"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                  <path
                      d="M8.3125 0.981587C8.66767 1.0545 8.97902 1.20558 9.2627 1.43374C9.48724 1.61438 9.73029 1.85933 9.97949 2.10854L14.707 6.83608L13.293 8.25014L9 3.95717V15.0431H7V3.95717L2.70703 8.25014L1.29297 6.83608L6.02051 2.10854C6.26971 1.85933 6.51277 1.61438 6.7373 1.43374C6.97662 1.24126 7.28445 1.04542 7.6875 0.981587C7.8973 0.94841 8.1031 0.956564 8.3125 0.981587Z"/>
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>
      <div class="input-footer">
        <span>Shift + Enter 换行</span>
        <span>Ctrl + N 新建对话</span>
      </div>
    </div>

    <!-- 右侧对话缩略表：常态是细刻度条，鼠标移上去展开成文字列表 -->
    <nav
        v-if="showRail"
        class="conv-rail"
        :class="{ 'is-scrollable': railScrollable, 'is-open': railOpen }"
        aria-label="对话快速定位"
        @mouseenter="railOpen = true"
        @mouseleave="railOpen = false"
        @focusin="railOpen = true"
        @focusout="railOpen = false"
    >
      <div ref="railEl" class="rail-scroll">
        <button
            v-for="turn in turns"
            :key="turn.index"
            type="button"
            class="rail-item"
            :class="{ active: turn.index === activeTurn }"
            :title="turn.text"
            @click="jumpToTurn(turn.index)"
        >
          <span class="rail-text">{{ turn.text }}</span>
        </button>
      </div>
    </nav>

  </div>
</template>

<script setup>
import {ref, computed, nextTick, onMounted, watch, onUnmounted} from 'vue'
import {api} from '@/utils/api'
import {useSessions} from "@/composables/useSessions.js";
import {useAuth} from "@/composables/useAuth.js";
import TripPlanCard from '@/components/chat/TripPlanCard.vue'
import TripPlanModal from '@/components/chat/TripPlanModal.vue'

const props = defineProps({
  threadId: {
    type: String,
    default: null
  },
  // 侧边栏是否收起：收起时头部换成左上角悬浮控制条
  sidebarCollapsed: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['new-chat', 'update-title', 'toggle-sidebar', 'thread-created', 'require-login'])

const {isLoggedIn, user} = useAuth()

// 用户头像用用户名首字母，比通用 👤 更有辨识度
const userLetter = computed(() => {
  const name = user.value?.username || '我'
  return name.slice(0, 1).toUpperCase()
})

// ===== 状态 =====
const messages = ref([])
const inputText = ref('')
const isLoading = ref(false)
const inputTextarea = ref(null)
const messagesWrapper = ref(null)
const bottomRef = ref(null)
const currentThreadId = ref(props.threadId)

// ===== 弹窗状态 =====
const showModal = ref(false)
const selectedPlan = ref(null)

// ===== 显示行程详情（打开弹窗） =====
const showPlanDetail = (plan) => {
  if (!plan) return
  selectedPlan.value = plan
  showModal.value = true
}

// ===== 关闭弹窗 =====
const closeModal = () => {
  showModal.value = false
}

// ===== 流式输出速度控制 =====
const displayBuffer = ref('')  // 待显示的内容缓冲区
const displayInterval = ref(null)  // 定时器
const DISPLAY_SPEED = 5  // 每次显示的字符数
const DISPLAY_INTERVAL = 40  // 显示间隔（毫秒）

const {
  loadSessions,          // 只用于刷新侧边栏
  loadSessionMessages,   // 用于加载消息（已存在）
  updateSessionTitle
} = useSessions()

// ===== 计算属性 =====
const currentTitle = computed(() => {
  if (messages.value.length === 0) return '新对话'
  const firstUserMsg = messages.value.find(m => m.role === 'user')
  if (firstUserMsg) {
    const content = firstUserMsg.content
    if (content.length > 20) return content.slice(0, 20) + '...'
    return content
  }
  return '新对话'
})

// ===== 右侧对话缩略表 =====
const railEl = ref(null)
const activeTurn = ref(null)
const railScrollable = ref(false)
// 常态收缩成刻度条，鼠标移入才展开（键盘聚焦同样展开）
const railOpen = ref(false)

// 每条用户提问 = 一个缩略项；index 是它在 messages 里的下标，用来定位气泡
const turns = computed(() =>
  messages.value
    .map((msg, index) => ({msg, index}))
    .filter(({msg}) => msg.role === 'user' && (msg.content || '').trim())
    .map(({msg, index}) => ({
      index,
      text: msg.content.replace(/\s+/g, ' ').trim()
    }))
)

// 只有一轮提问时跳转没意义，至少两轮才显示
const showRail = computed(() => turns.value.length >= 2)

const findTurnEl = (index) =>
  messagesWrapper.value?.querySelector(`[data-turn="${index}"]`) || null

// 以消息区顶部往下 80px 为参考线，判断当前滚到哪一轮
const updateActiveTurn = () => {
  const wrap = messagesWrapper.value
  if (!wrap || !turns.value.length) return
  const line = wrap.getBoundingClientRect().top + 80
  let current = turns.value[0].index
  for (const turn of turns.value) {
    const el = findTurnEl(turn.index)
    if (!el) continue
    if (el.getBoundingClientRect().top <= line) current = turn.index
    else break
  }
  activeTurn.value = current
}

// 滚动事件按帧节流，避免高频 getBoundingClientRect
let scrollTick = false
const onMessagesScroll = () => {
  if (scrollTick) return
  scrollTick = true
  requestAnimationFrame(() => {
    scrollTick = false
    updateActiveTurn()
  })
}

// 内容不足一屏时不加淡出遮罩，避免首尾条目发灰
const measureRail = () => {
  const rail = railEl.value
  railScrollable.value = !!rail && rail.scrollHeight > rail.clientHeight + 2
}

// 缩略表自身滚动，让当前项始终留在可视范围中间
const syncRailScroll = () => {
  const rail = railEl.value
  if (!rail) return
  measureRail()
  const idx = turns.value.findIndex(t => t.index === activeTurn.value)
  const item = idx >= 0 ? rail.children[idx] : null
  if (!item) return
  const target = item.offsetTop - rail.clientHeight / 2 + item.offsetHeight / 2
  rail.scrollTo({top: Math.max(0, target), behavior: 'smooth'})
}

// 点击缩略项 → 平滑滚到对应提问
const jumpToTurn = (index) => {
  const wrap = messagesWrapper.value
  const el = findTurnEl(index)
  if (!wrap || !el) return

  const top = el.getBoundingClientRect().top - wrap.getBoundingClientRect().top + wrap.scrollTop - 18
  wrap.scrollTo({top: Math.max(0, top), behavior: 'smooth'})
  activeTurn.value = index

  // 目标气泡闪一下，跳过去不至于找不到落点
  const bubble = el.querySelector('.bubble-user')
  if (bubble) {
    bubble.classList.add('is-jump')
    setTimeout(() => bubble.classList.remove('is-jump'), 1200)
  }
}

watch(activeTurn, () => nextTick(syncRailScroll))

// 展开/收起时条目高度会变，重新把当前项滚到可视区
watch(railOpen, () => nextTick(syncRailScroll))

// 消息增减（流式回复、切换会话）后重算当前项
watch(() => turns.value.length, async () => {
  await nextTick()
  updateActiveTurn()
  measureRail()
})

// ===== 快捷示例 =====
const quickExamples = [
  {icon: '🏖️', label: '三亚 3 天度假', text: '帮我规划三亚3天度假，预算5000，喜欢海滩和海鲜'},
  {icon: '🏔️', label: '成都 5 天深度游', text: '我想去成都玩5天，预算6000，喜欢美食和熊猫'},
  {icon: '🎨', label: '大理 4 天文艺之旅', text: '大理4天慢生活，预算4000，喜欢拍照和咖啡馆'}
]

// ===== 工具函数 =====
const formatTime = (timestamp) => {
  if (!timestamp) return ''
  const date = new Date(timestamp)
  return date.toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'})
}

const scrollToBottom = () => {
  nextTick(() => {
    bottomRef.value?.scrollIntoView({behavior: 'smooth'})
  })
}

// ===== 加载会话消息 =====
const loadSessionMessagesFromDB = async (threadId) => {
  if (!threadId) return
  try {
    const msgs = await loadSessionMessages(threadId)

    if (!msgs || msgs.length === 0) {
      messages.value = []
      return
    }

    messages.value = msgs.map(msg => {
      // 基础消息对象
      const message = {
        role: msg.type === 'human' || msg.role === 'user' ? 'user' : 'assistant',
        content: msg.content || '',
        timestamp: msg.timestamp || new Date().toISOString(),
        tripPlan: null
      }

      // 如果是 AI 消息
      if (message.role === 'assistant') {
        // 1. 从 content 解析行程
        if (message.content) {
          const plan = parseTripPlanFromMessage(message.content)
          if (plan) {
            message.tripPlan = plan
          }
        }

        // ✅ 2. 如果数据库里存了 map_data，附加到 tripPlan 上
        if (message.tripPlan && msg.map_data) {
          try {
            const mapData = typeof msg.map_data === 'string'
              ? JSON.parse(msg.map_data)
              : msg.map_data
            message.tripPlan.map_data = mapData
            console.log('✅ 附加 map_data:', mapData.attractions?.length || 0, '个景点')
          } catch (e) {
            console.warn('⚠️ 解析 map_data 失败:', e)
          }
        }
      }

      return message
    })

    // 日志统计
    const planCount = messages.value.filter(m => m.tripPlan).length
    const mapCount = messages.value.filter(m => m.tripPlan?.map_data).length
    console.log(`📊 加载完成: ${messages.value.length} 条消息, ${planCount} 个行程, ${mapCount} 个地图`)

    scrollToBottom()

  } catch (error) {
    console.error('加载会话消息失败:', error)
  }
}

// ===== 清空消息 =====
const clearMessages = () => {
  messages.value = []
}

// ===== 聚焦输入框 =====
const focusInput = () => {
  nextTick(() => {
    inputTextarea.value?.focus()
  })
}

// ===== 思考步骤管理 =====
const addThinkingStep = (aiMessage, node, message, status = 'running') => {
  if (!aiMessage.thinkingSteps) {
    aiMessage.thinkingSteps = []
  }

  // 检查是否已存在相同节点
  const existingIndex = aiMessage.thinkingSteps.findIndex(s => s.node === node)
  if (existingIndex !== -1) {
    // 更新已有步骤
    aiMessage.thinkingSteps[existingIndex] = {
      ...aiMessage.thinkingSteps[existingIndex],
      message: message,
      status: status,
      timestamp: Date.now()
    }
  } else {
    // 添加新步骤
    aiMessage.thinkingSteps.push({
      node: node,
      message: message,
      status: status,
      timestamp: Date.now(),
      duration: null
    })
  }

  // 标记正在思考
  aiMessage.isThinking = true
  aiMessage.showThinking = true

  // 更新消息列表
  const currentIndex = messages.value.length - 1
  if (currentIndex >= 0 && messages.value[currentIndex]?.role === 'assistant') {
    messages.value[currentIndex] = {
      ...messages.value[currentIndex],
      thinkingSteps: [...aiMessage.thinkingSteps],
      isThinking: aiMessage.isThinking,
      showThinking: aiMessage.showThinking
    }
  }
}

const completeThinkingStep = (aiMessage, node, message) => {
  if (!aiMessage.thinkingSteps) return

  const stepIndex = aiMessage.thinkingSteps.findIndex(s => s.node === node)
  if (stepIndex !== -1) {
    const step = aiMessage.thinkingSteps[stepIndex]
    const duration = Date.now() - step.timestamp
    aiMessage.thinkingSteps[stepIndex] = {
      ...step,
      message: message || step.message,
      status: 'done',
      duration: duration
    }

    // 更新消息列表
    const currentIndex = messages.value.length - 1
    if (currentIndex >= 0 && messages.value[currentIndex]?.role === 'assistant') {
      messages.value[currentIndex] = {
        ...messages.value[currentIndex],
        thinkingSteps: [...aiMessage.thinkingSteps]
      }
    }
  }
}

const errorThinkingStep = (aiMessage, node, message) => {
  if (!aiMessage.thinkingSteps) return

  const stepIndex = aiMessage.thinkingSteps.findIndex(s => s.node === node)
  if (stepIndex !== -1) {
    aiMessage.thinkingSteps[stepIndex] = {
      ...aiMessage.thinkingSteps[stepIndex],
      message: message || '执行失败',
      status: 'error'
    }

    // 更新消息列表
    const currentIndex = messages.value.length - 1
    if (currentIndex >= 0 && messages.value[currentIndex]?.role === 'assistant') {
      messages.value[currentIndex] = {
        ...messages.value[currentIndex],
        thinkingSteps: [...aiMessage.thinkingSteps]
      }
    }
  }
}

// ===== 切换思考过程显示 =====
const toggleThinking = (index) => {
  const msg = messages.value[index]
  if (msg) {
    msg.showThinking = !msg.showThinking
    // 触发响应式更新
    messages.value[index] = {...msg}
  }
}

// ===== 处理 SSE 事件 =====
const handleSSEEvent = (data, aiMessage) => {
  console.log('📨 收到 SSE 事件:', data)

  const currentIndex = messages.value.length - 1

  switch (data.type) {
    case 'node_start':
      // ✅ 节点开始 - 添加到思考步骤
      if (isLoading.value) {
        isLoading.value = false
      }

      // 映射节点名称到更友好的显示名称
      const nodeMap = {
        'classify_input': '🔍 分析意图',
        'format_input': '📋 提取需求',
        'retrieve_guides': '📚 检索攻略',
        'search_attractions': '🏛️ 搜索景点',
        'get_weather': '🌤️ 获取天气',
        'generate_plan': '📝 生成行程',
        'clarify': '❓ 补充信息',
        'chat': '💬 思考回复'
      }

      const displayNode = nodeMap[data.node] || data.node
      addThinkingStep(aiMessage, displayNode, data.message || '正在处理...', 'running')
      break

    case 'node_end':
      // ✅ 节点结束 - 标记完成
      const nodeMapEnd = {
        'classify_input': '🔍 分析意图',
        'format_input': '📋 提取需求',
        'retrieve_guides': '📚 检索攻略',
        'search_attractions': '🏛️ 搜索景点',
        'get_weather': '🌤️ 获取天气',
        'generate_plan': '📝 生成行程',
        'clarify': '❓ 补充信息',
        'chat': '💬 思考回复'
      }
      const displayNodeEnd = nodeMapEnd[data.node] || data.node
      completeThinkingStep(aiMessage, displayNodeEnd, data.message || '完成')
      break

    case 'chunk':
      // ✅ 流式文本输出
      if (isLoading.value) {
        isLoading.value = false
      }

      // 将新内容加入缓冲区
      displayBuffer.value += data.content || ''

      // 如果还没有启动定时器，则启动
      if (!displayInterval.value) {
        startDisplayTimer(aiMessage)
      }
      break

    case 'message':
      if (isLoading.value) {
        isLoading.value = false
      }

      displayBuffer.value += data.content || ''

      if (!displayInterval.value) {
        startDisplayTimer(aiMessage)
      }
      break

    case 'trip_plan':
      console.log('📦 收到 trip_plan 事件:', data)
      if (isLoading.value) {
        isLoading.value = false
      }

      const plan = data.plan
      console.log('📋 plan 数据:', plan)
      console.log('🗺️ map_data 是否存在:', !!plan.map_data)
      if (plan.map_data) {
        console.log('🗺️ map_data 内容:', plan.map_data)
        console.log('📍 景点数量:', plan.map_data.attractions?.length)
      }
      if (plan && currentIndex >= 0 && messages.value[currentIndex]?.role === 'assistant') {
        const tripPlan = convertToTripPlan(plan)
        console.log('📋 转换后的 tripPlan:', tripPlan)
        console.log('🗺️ tripPlan.map_data:', tripPlan.map_data)
        const currentMsg = messages.value[currentIndex]

        stopDisplayTimer()

        // ✅ 标记思考完成，自动折叠
        aiMessage.isThinking = false
        aiMessage.showThinking = false  // ✅ 折叠思考过程

        messages.value[currentIndex] = {
          ...currentMsg,
          tripPlan: tripPlan,
          isProcessing: false,
          isThinking: false,
          showThinking: false  // ✅ 折叠思考过程
        }
        aiMessage.tripPlan = tripPlan
        aiMessage.isProcessing = false
      }
      break

    case 'done':
      isLoading.value = false

      // ✅ 强制刷新缓冲区剩余内容
      flushDisplayBuffer(aiMessage)
      stopDisplayTimer()

      if (currentIndex >= 0 && messages.value[currentIndex]?.role === 'assistant') {
        const currentMsg = messages.value[currentIndex]

        let tripPlan = currentMsg.tripPlan
        if (!tripPlan && currentMsg.content) {
          tripPlan = parseTripPlanFromMessage(currentMsg.content)
        }

        // ✅ 标记思考完成，并且自动折叠
        aiMessage.isThinking = false
        aiMessage.showThinking = false  // ✅ 设置为 false，自动折叠

        messages.value[currentIndex] = {
          ...currentMsg,
          isProcessing: false,
          isThinking: false,
          showThinking: false,  // ✅ 折叠思考过程
          tripPlan: tripPlan || currentMsg.tripPlan
        }

        if (tripPlan) {
          aiMessage.tripPlan = tripPlan
        }
        aiMessage.isProcessing = false
      }

      setTimeout(() => {
        loadSessions()
      }, 100)
      break

    case 'warning':
      // 半失败：例如攻略检索挂了但行程照常生成。
      // 只在思考链里把对应节点标红，不动正文、不中断流（'error' 那种替换正文的处理
      // 用在这里会让用户以为整条消息失败了）。
      console.warn('⚠️ SSE 警告:', data.node, data.message)
      const warnNodeMap = {
        'classify_input': '🔍 分析意图',
        'format_input': '📋 提取需求',
        'retrieve_guides': '📚 检索攻略',
        'search_attractions': '🏛️ 搜索景点',
        'get_weather': '🌤️ 获取天气',
        'generate_plan': '📝 生成行程',
        'clarify': '❓ 补充信息',
        'chat': '💬 思考回复'
      }
      addThinkingStep(aiMessage, warnNodeMap[data.node] || data.node, data.message || '执行有异常', 'error')
      break

    case 'error':
      console.error('❌ SSE 错误:', data.error || data.message)
      isLoading.value = false
      stopDisplayTimer()

      // ✅ 标记错误
      const errorNode = '❌ 处理出错'
      errorThinkingStep(aiMessage, errorNode, data.error || data.message)

      if (currentIndex >= 0 && messages.value[currentIndex]?.role === 'assistant') {
        // ✅ 出错时也折叠思考过程
        aiMessage.isThinking = false
        aiMessage.showThinking = false

        messages.value[currentIndex] = {
          ...messages.value[currentIndex],
          content: `错误: ${data.error || data.message}`,
          isProcessing: false,
          isThinking: false,
          showThinking: false
        }
        aiMessage.content = `错误: ${data.error || data.message}`
        aiMessage.isProcessing = false
      }
      break

    default:
      if (data.chunk) {
        displayBuffer.value += data.chunk || ''
        if (!displayInterval.value) {
          startDisplayTimer(aiMessage)
        }
      }
      if (data.done) {
        isLoading.value = false
        flushDisplayBuffer(aiMessage)
        stopDisplayTimer()

        if (currentIndex >= 0 && messages.value[currentIndex]?.role === 'assistant') {
          // ✅ 折叠思考过程
          aiMessage.isThinking = false
          aiMessage.showThinking = false

          messages.value[currentIndex] = {
            ...messages.value[currentIndex],
            isProcessing: false,
            isThinking: false,
            showThinking: false
          }
          aiMessage.isProcessing = false
          aiMessage.isThinking = false
        }
        setTimeout(() => {
          loadSessions()
        }, 100)
      }
      if (data.plan && !data.type) {
        const plan = convertToTripPlan(data.plan)
        if (plan && currentIndex >= 0 && messages.value[currentIndex]?.role === 'assistant') {
          const currentMsg = messages.value[currentIndex]
          stopDisplayTimer()

          // ✅ 折叠思考过程
          aiMessage.showThinking = false
          aiMessage.isThinking = false

          messages.value[currentIndex] = {
            ...currentMsg,
            tripPlan: plan,
            isThinking: false,
            showThinking: false
          }
          aiMessage.tripPlan = plan
          aiMessage.isThinking = false
        }
      }
      break
  }
}

// ===== 刷新缓冲区 =====
const flushDisplayBuffer = (aiMessage) => {
  if (displayBuffer.value.length === 0) return

  aiMessage.content += displayBuffer.value
  displayBuffer.value = ''

  const lastMsg = messages.value[messages.value.length - 1]
  if (lastMsg && lastMsg.role === 'assistant') {
    lastMsg.content = aiMessage.content
  }

  scrollToBottom()
}

// ===== 启动显示定时器 =====
const startDisplayTimer = (aiMessage) => {
  if (displayInterval.value) {
    clearInterval(displayInterval.value)
    displayInterval.value = null
  }

  displayInterval.value = setInterval(() => {
    if (displayBuffer.value.length === 0) {
      stopDisplayTimer()
      return
    }

    const charsToShow = Math.min(DISPLAY_SPEED, displayBuffer.value.length)
    const chunk = displayBuffer.value.slice(0, charsToShow)
    displayBuffer.value = displayBuffer.value.slice(charsToShow)

    aiMessage.content += chunk

    const currentIndex = messages.value.length - 1
    if (currentIndex >= 0 && messages.value[currentIndex]?.role === 'assistant') {
      const currentMsg = messages.value[currentIndex]
      messages.value[currentIndex] = {
        ...currentMsg,
        content: aiMessage.content
      }
    }

    scrollToBottom()
  }, DISPLAY_INTERVAL)
}

// ===== 停止显示定时器 =====
const stopDisplayTimer = () => {
  if (displayInterval.value) {
    clearInterval(displayInterval.value)
    displayInterval.value = null
  }
}

// ===== 欢迎页快捷示例：填入输入框后复用 handleSend =====
// 不要写成 @click="handleSend(example.text)"：handleSend 是无参函数，而 166 行的
// 发送按钮是 @click="handleSend"，那样会把点击事件对象当成文本传进来。
const handleExample = (text) => {
  inputText.value = text
  handleSend()
}

// ===== 发送消息 =====
const handleSend = async () => {
  const text = inputText.value.trim()
  if (!text || isLoading.value) return

  // 未登录不发请求：留住输入框里的文字，只唤起登录窗
  if (!isLoggedIn.value) {
    emit('require-login')
    return
  }

  displayBuffer.value = ''
  stopDisplayTimer()

  messages.value.push({
    role: 'user',
    content: text,
    timestamp: new Date().toISOString()
  })
  inputText.value = ''
  isLoading.value = true

  // ✅ 创建 AI 消息占位，默认展开思考过程
  const aiMessage = {
    role: 'assistant',
    content: '',
    timestamp: new Date().toISOString(),
    isProcessing: true,
    isThinking: true,
    showThinking: true,  // ✅ 默认展开
    thinkingSteps: [
      {
        node: '初始化',
        message: '准备处理你的请求...',
        status: 'running',
        timestamp: Date.now(),
        duration: null
      }
    ],
    nodeStatus: {
      node: '准备中',
      message: '正在思考...',
      status: 'running'
    },
    tripPlan: null
  }
  messages.value.push(aiMessage)
  scrollToBottom()

  const threadId = props.threadId || currentThreadId.value

  try {
    const response = await api.sendMessage(threadId, text)

    if (!response.ok) {
      const errorText = await response.text()
      throw new Error(`HTTP ${response.status}: ${errorText}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const {done, value} = await reader.read()
      if (done) break

      buffer += decoder.decode(value, {stream: true})
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))

            if (data.thread_id && data.thread_id !== threadId) {
              currentThreadId.value = data.thread_id
              emit('thread-created', data.thread_id)
            }

            handleSSEEvent(data, aiMessage)

            const lastMsg = messages.value[messages.value.length - 1]
            if (lastMsg && lastMsg.role === 'assistant') {
              lastMsg.content = aiMessage.content
              lastMsg.tripPlan = aiMessage.tripPlan
            }

            if (data.done) {
              await loadSessions()
            }
            scrollToBottom()
          } catch (e) {
            // 忽略非 JSON 行
          }
        }
      }
    }
  } catch (error) {
    console.error('❌ 发送失败:', error)
    aiMessage.content = `抱歉，出错了: ${error.message || '请重试'}`
    isLoading.value = false
  }
}

const parseTripPlanFromMessage = (content) => {
  if (!content || typeof content !== 'string') return null

  try {
    // 方法1: 查找 JSON 代码块 (```json ... ```)
    let jsonMatch = content.match(/```json\s*([\s\S]*?)\s*```/)
    if (jsonMatch) {
      try {
        // 注意：命中的代码块不一定就是行程。generate_plan 的正文里可能跟了第二段
        // JSON（景点提取结果 {"attractions": [...]}），这时 convertToTripPlan 返回
        // null，必须继续尝试后面的格式，不能直接 return 出去——否则整条消息退化成
        // 显示原始 JSON（历史消息最明显）。
        const plan = convertToTripPlan(JSON.parse(jsonMatch[1]))
        if (plan) return plan
      } catch (e) {
        console.warn('⚠️ JSON 代码块解析失败:', e)
      }
    }

    // 方法2: 查找 JSON 代码块 (``` ... ```) 不带 json 标记
    jsonMatch = content.match(/```\s*([\s\S]*?)\s*```/)
    if (jsonMatch) {
      try {
        const plan = convertToTripPlan(JSON.parse(jsonMatch[1]))
        if (plan) return plan
      } catch (e) {
        // 不是 JSON，忽略
      }
    }

    // 方法3: 直接查找 JSON 对象
    // 使用更通用的匹配方式
    const startIndex = content.indexOf('{')
    if (startIndex !== -1) {
      let braceCount = 0
      let endIndex = startIndex
      let inString = false
      let escapeNext = false

      for (let i = startIndex; i < content.length; i++) {
        const char = content[i]

        if (escapeNext) {
          escapeNext = false
          continue
        }

        if (char === '\\') {
          escapeNext = true
          continue
        }

        if (char === '"') {
          inString = !inString
          continue
        }

        if (!inString) {
          if (char === '{') braceCount++
          if (char === '}') {
            braceCount--
            if (braceCount === 0) {
              endIndex = i + 1
              break
            }
          }
        }
      }

      if (endIndex > startIndex) {
        const jsonStr = content.substring(startIndex, endIndex)
        try {
          const plan = convertToTripPlan(JSON.parse(jsonStr))
          if (plan) return plan
        } catch (e) {
          // 忽略解析失败
        }
      }
    }

    return null
  } catch (error) {
    console.warn('⚠️ 解析行程数据失败:', error)
    return null
  }
}

const convertToTripPlan = (data) => {
  // 检查必要字段
  if (!data) return null

  // ✅ 兼容 days 和 itinerary 两种字段名
  let daysData = data.days || data.itinerary || data.daily_itinerary

  if (!daysData || !Array.isArray(daysData) || daysData.length === 0) {
    return null
  }
  const result = {
    id: Date.now() + Math.random() * 1000,
    destination: data.destination || '未知目的地',
    totalDays: data.totalDays || data.days?.length || data.itinerary?.length || 3,
    budget: data.budget || data.total_budget || 0,
    startDate: daysData[0]?.date || data.start_date || '待定',
    rating: data.rating || 4.5,
    weather: daysData[0]?.weather || data.weather_summary || data.weather || '',
    days: daysData.map((day, index) => ({
      day: day.day || index + 1,
      theme: day.theme || `第 ${day.day || index + 1} 天`,
      // ✅ 支持 activity 和 name 两种字段
      attractions: (day.activities || day.attractions || []).map(act => ({
        name: act.activity || act.name || '未命名活动',
        time: act.time || act.start_time || '待定',
        cost: act.cost || act.cost_estimate || 0,
        description: act.description || act.notes || ''
      })),
      meals: (day.activities || day.attractions || [])
          ?.filter(a => {
            const name = a.activity || a.name || ''
            return name.includes('餐') || name.includes('吃') ||
                name.includes('午餐') || name.includes('晚餐') ||
                name.includes('早餐') || name.includes('美食')
          })
          .map(a => a.activity || a.name) || [],
      hotel: day.hotel || day.accommodation || '',
      notes: day.notes || '',
      estimatedCost: day.estimatedCost || day.estimated_cost?.total || 0
    })),
    highlights: data.tips || data.travel_tips || data.highlights || ['特色美食', '文化体验', '舒适住宿'],
    budgetSummary: data.budgetSummary || data.budget_summary || null
  }
   // ✅ 添加地图数据
  if (data.map_data) {
    result.map_data = data.map_data
    console.log('🗺️ map_data 已添加到 tripPlan:', result.map_data)
  } else {
    console.log('⚠️ 没有 map_data')
  }

  return result
}

// ===== 自动调整输入框 =====
const autoResize = () => {
  const el = inputTextarea.value
  if (el) {
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }
}

// ===== 暴露方法 =====
defineExpose({
  loadSessionMessages: loadSessionMessagesFromDB,
  clearMessages,
  focusInput
})

// ===== 监听 threadId 变化 =====
watch(() => props.threadId, (newId) => {
  if (newId) {
    currentThreadId.value = newId
    loadSessionMessagesFromDB(newId)
    loadSessions()
  } else {
    messages.value = []
  }
}, {immediate: true})

// ===== 挂载时聚焦 =====
onMounted(() => {
  focusInput()
  nextTick(() => {
    messagesWrapper.value?.addEventListener('scroll', onMessagesScroll, {passive: true})
    updateActiveTurn()
    measureRail()
  })
  window.addEventListener('resize', measureRail)
})
// ===== 在组件卸载时清理定时器 =====
onUnmounted(() => {
  stopDisplayTimer()
  messagesWrapper.value?.removeEventListener('scroll', onMessagesScroll)
  window.removeEventListener('resize', measureRail)
})
</script>

<style scoped>
/* ===== 容器 ===== */
.chat-container {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #ffffff;
  position: relative;
  overflow: hidden;
  /* 右侧缩略条占的宽度（26 的刻度条 + 两侧留白） */
  --rail-space: 36px;
}

/* 有缩略条时：消息区和输入框一起向左让 36px，避免刻度条压到气泡右边缘 */
.chat-container.with-rail .messages-wrapper {
  padding-right: var(--rail-space);
}

.chat-container.with-rail .input-wrapper {
  right: calc(var(--rail-space) + 10px);
}

/* ===== 头部 ===== */
.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  border-bottom: 1px solid #eef0f4;
  flex-shrink: 0;
  background: #ffffff;
  z-index: 5;
  min-height: 58px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.menu-btn {
  display: none;
  background: none;
  border: none;
  padding: 6px;
  cursor: pointer;
  color: #6b7280;
  border-radius: 6px;
  transition: all 0.2s ease;
}

.menu-btn:hover {
  background: #f7f8fa;
}

.chat-title {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: #1a1a1a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

/* ===== 侧边栏收起后的左上角控制条 ===== */
.chat-header.is-collapsed {
  padding-left: 14px;
}

.float-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.fb-logo {
  font-size: 22px;
  line-height: 1;
  user-select: none;
}

.fb-pill {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  background: #ffffff;
  border: 1px solid #e8ebf2;
  border-radius: 999px;
  box-shadow: 0 2px 10px rgba(23, 32, 60, 0.07);
}

.fb-btn {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: #262b33;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

/* 「打开边栏」是主操作，常驻一层浅色底，和参考图一致 */
.fb-btn.is-primary {
  background: #f1f3f7;
}

.fb-btn:hover {
  background: #eceff5;
}

.fb-btn.is-primary:hover {
  background: #e5eaf2;
  color: #4f6ef7;
}

/* 深色气泡提示 */
.fb-btn[data-tip]::after {
  content: attr(data-tip);
  position: absolute;
  top: calc(100% + 9px);
  left: 50%;
  padding: 6px 10px;
  border-radius: 7px;
  background: #24282f;
  color: #ffffff;
  font-size: 12px;
  font-weight: 400;
  line-height: 1.2;
  letter-spacing: 0;
  white-space: nowrap;
  transform: translate(-50%, -3px);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.14s ease, transform 0.14s ease;
  z-index: 40;
}

.fb-btn[data-tip]:hover::after {
  opacity: 1;
  transform: translate(-50%, 0);
}

.header-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  color: #6b7280;
  border-radius: 9px;
  transition: all 0.18s ease;
}

.header-btn:hover {
  background: #f1f3f9;
  color: #4f6ef7;
}

/* ===== 消息区域 ===== */
.messages-wrapper {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 24px 0 24px;
  background: #f7f8fa;
  min-height: 0;
  scroll-behavior: smooth;
  /* ✅ Firefox 滚动条 */
  scrollbar-width: thin;
  scrollbar-color: rgba(0, 0, 0, 0.12) transparent;
}

/* ✅ DeepSeek 风格滚动条 - Webkit */
.messages-wrapper::-webkit-scrollbar {
  width: 6px;
}

.messages-wrapper::-webkit-scrollbar-track {
  background: transparent;
  margin: 4px 0;
}

.messages-wrapper::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.12);
  border-radius: 3px;
  min-height: 30px;
}

.messages-wrapper::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 0, 0, 0.25);
}

.messages-container {
  max-width: 820px;
  margin: 0 auto;
  padding: 0 24px 150px;
}

/* ===== 右侧对话缩略表（常态为细刻度条，悬停展开） ===== */
.conv-rail {
  position: absolute;
  top: 50%;
  right: 6px;
  transform: translateY(-50%);
  z-index: 8;
  display: flex;
  width: 26px;
  max-height: calc(100% - 190px);
  background: transparent;
  border: 1px solid transparent;
  border-radius: 16px;
  box-shadow: none;
  overflow: hidden;
  transition: width 0.22s ease, background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}

.conv-rail.is-open {
  width: 214px;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-color: #e9ecf3;
  box-shadow: 0 14px 36px rgba(23, 32, 60, 0.12);
}

.rail-scroll {
  position: relative; /* 作为 item.offsetTop 的基准 */
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 0;
  scrollbar-width: none;
}

.conv-rail.is-open .rail-scroll {
  padding: 10px 8px;
}

/* 只有内容超出一屏才做上下淡出，暗示还能滚 */
.conv-rail.is-scrollable .rail-scroll {
  -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 14px, #000 calc(100% - 14px), transparent 100%);
  mask-image: linear-gradient(to bottom, transparent 0, #000 14px, #000 calc(100% - 14px), transparent 100%);
}

.rail-scroll::-webkit-scrollbar {
  display: none;
}

.rail-item {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-shrink: 0;
  width: 100%;
  height: 14px;
  padding: 0 7px;
  border: none;
  border-radius: 9px;
  background: transparent;
  color: #98a1b3;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.5;
  text-align: right;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

/* 收起态：每条提问显示成一根小横线，当前那条加粗变深 */
.rail-item::before {
  content: '';
  width: 12px;
  height: 2px;
  border-radius: 2px;
  background: #d5dae3;
  transition: background 0.15s ease, width 0.15s ease;
}

.rail-item:hover::before {
  background: #9aa3b5;
}

.rail-item.active::before {
  width: 14px;
  background: #24282f;
}

.rail-text {
  display: none;
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ===== 展开态 ===== */
.conv-rail.is-open .rail-item {
  height: auto;
  /* 右侧多留 10px，给 active 的小竖条让出位置 */
  padding: 7px 16px 7px 10px;
}

.conv-rail.is-open .rail-item::before {
  display: none;
}

.conv-rail.is-open .rail-text {
  display: block;
  /* 等宽度动画走完再淡入，避免文字在展开过程中被挤成一团 */
  animation: railTextIn 0.12s ease 0.12s both;
}

.conv-rail.is-open .rail-item:hover {
  background: rgba(79, 110, 247, 0.06);
  color: #1f2937;
}

.conv-rail.is-open .rail-item.active {
  background: #ffffff;
  color: #111827;
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(23, 32, 60, 0.07), inset 0 0 0 1px #e6eaf3;
}

/* 当前项右侧的小竖条，和截图里的标记一致 */
.conv-rail.is-open .rail-item.active::after {
  content: '';
  position: absolute;
  top: 50%;
  right: 6px;
  width: 2px;
  height: 12px;
  border-radius: 2px;
  background: #4f6ef7;
  transform: translateY(-50%);
}

@keyframes railTextIn {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

/* 跳转后目标气泡闪一下 */
.bubble-user.is-jump {
  animation: jumpFlash 1.2s ease;
}

@keyframes jumpFlash {
  0% {
    box-shadow: 0 0 0 0 rgba(79, 110, 247, 0.5);
  }
  35% {
    box-shadow: 0 0 0 6px rgba(79, 110, 247, 0.14);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(79, 110, 247, 0);
  }
}

/* ===== 欢迎界面 ===== */
.welcome-screen {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  /* 让空状态在可视区内垂直居中，同时给底部输入框留出空间 */
  min-height: calc(100vh - 330px);
  text-align: center;
  padding: 24px 20px;
}

.welcome-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 88px;
  height: 88px;
  margin-bottom: 22px;
  border-radius: 26px;
  font-size: 40px;
  background: linear-gradient(135deg, #eef2ff 0%, #f4efff 100%);
  border: 1px solid #e6eaff;
  box-shadow: 0 12px 32px rgba(79, 110, 247, 0.12);
  animation: bounce 3s ease-in-out infinite;
}

.welcome-title {
  font-size: 26px;
  font-weight: 600;
  letter-spacing: -0.02em;
  margin-bottom: 10px;
  background: linear-gradient(135deg, #4f6ef7, #7c3aed);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.welcome-subtitle {
  font-size: 14px;
  color: #6b7280;
  margin-bottom: 28px;
}

.quick-examples {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: center;
}

.example-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px 10px 10px;
  background: #ffffff;
  border: 1px solid #e8eaf1;
  border-radius: 12px;
  color: #1a1a1a;
  cursor: pointer;
  transition: all 0.18s ease;
  font-size: 13px;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.03);
}

.example-btn:hover {
  border-color: #c7d2fe;
  box-shadow: 0 8px 22px rgba(79, 110, 247, 0.12);
  transform: translateY(-2px);
}

.example-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: #f5f6ff;
  font-size: 16px;
  flex-shrink: 0;
}

/* ===== 消息样式 ===== */
.message-wrapper {
  display: flex;
  margin-bottom: 26px;
}

.message-ai {
  display: flex;
  gap: 12px;
  width: 100%;
  align-items: flex-start;
}

/* 用户消息整体靠右：内容宽度由气泡撑开，不能再被 flex:1 拉满 */
.message-user {
  display: flex;
  gap: 12px;
  width: 100%;
  justify-content: flex-end;
  align-items: flex-start;
}

.avatar {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: 600;
  flex-shrink: 0;
  user-select: none;
}

.avatar-ai {
  background: linear-gradient(135deg, #eef2ff, #e7ecff);
  border: 1px solid #e2e8ff;
}

.avatar-user {
  background: linear-gradient(135deg, #5b78ff, #7c3aed);
  color: #ffffff;
  font-size: 13px;
  box-shadow: 0 4px 12px rgba(79, 110, 247, 0.26);
}

/* AI 内容吃满剩余宽度；用户内容按气泡自身宽度收缩 */
.message-ai .message-content {
  flex: 1;
  min-width: 0;
  max-width: 100%;
  padding-top: 2px;
}

.message-user .message-content {
  flex: 0 1 auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  max-width: min(72%, 560px);
  min-width: 0;
  padding-top: 2px;
}

.message-text {
  word-wrap: break-word;
  overflow-wrap: break-word;
}

.message-bubble {
  padding: 13px 17px;
  border-radius: 16px;
  font-size: 14px;
  line-height: 1.72;
  word-break: break-word;
}

.bubble-ai {
  background: #ffffff;
  color: #1a1a1a;
  border: 1px solid #eceef3;
  border-bottom-left-radius: 6px;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
}

/* 纯文本回复按内容收缩，短句不再拉成一整条白板 */
.message-ai .bubble-ai:not(.bubble-plain) {
  width: fit-content;
  max-width: 100%;
}

/* 行程卡片自带版式：去掉气泡壳，避免白色描边把卡片再套一圈 */
.bubble-plain {
  padding: 0;
  background: transparent;
  border: none;
  box-shadow: none;
  border-radius: 0;
}

.bubble-user {
  max-width: 100%;
  background: linear-gradient(135deg, #5b78ff 0%, #4f6ef7 100%);
  color: #ffffff;
  border-bottom-right-radius: 6px;
  box-shadow: 0 6px 18px rgba(79, 110, 247, 0.22);
  white-space: pre-wrap;
}

.message-time {
  font-size: 11px;
  color: #aab0bd;
  margin-top: 7px;
  padding-right: 2px;
  text-align: right;
}

/* ===== 打字动画 ===== */
.typing-indicator {
  display: flex;
  gap: 4px;
  padding: 4px 0;
}

.typing-indicator span {
  width: 7px;
  height: 7px;
  background: #9ca3af;
  border-radius: 50%;
  animation: pulse 1.4s ease-in-out infinite;
}

.typing-indicator span:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-indicator span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}

@keyframes bounce {
  0%, 100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-10px);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(12px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.fade-in-up {
  animation: fadeInUp 0.3s ease forwards;
}

/* ===== 输入框 - 居中固定底部 ===== */
.input-wrapper {
  position: absolute;
  bottom: 0;
  left: 10px;
  right: 10px;
  padding: 0 20px 12px;
  background: linear-gradient(to top, #f7f8fa 78%, rgba(247, 248, 250, 0));
  z-index: 10;
  pointer-events: none;
}

.input-container {
  max-width: 820px;
  margin: 0 auto;
  pointer-events: auto;
  background: #ffffff;
  border-radius: 18px;
  border: 1px solid #e6e8ef;
  box-shadow: 0 8px 30px rgba(17, 24, 39, 0.07);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
  padding: 8px 12px 8px 16px;
}

.input-container:focus-within {
  border-color: #4f6ef7;
  box-shadow: 0 8px 30px rgba(79, 110, 247, 0.14), 0 0 0 4px rgba(79, 110, 247, 0.10);
}

.input-inner {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.chat-input {
  width: 100%;
  border: none;
  background: transparent;
  padding: 6px 0;
  font-size: 15px;
  line-height: 1.6;
  resize: none;
  outline: none;
  font-family: inherit;
  color: #1a1a1a;
  max-height: 200px;
  min-height: 40px;
}

.chat-input::placeholder {
  color: #9ca3af;
}

.chat-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 输入框底部工具栏 */
.input-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 0 2px;
}

.toolbar-left {
  display: flex;
  gap: 4px;
}

.toolbar-right {
  display: flex;
  gap: 4px;
  align-items: center;
}

/* 发送按钮 */
.send-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: none;
  border-radius: 50%;
  background: #eef0f5;
  color: #a8afbd;
  cursor: pointer;
  transition: all 0.18s ease;
}

.send-btn.active {
  background: linear-gradient(135deg, #5b78ff, #4f6ef7);
  color: white;
  box-shadow: 0 6px 16px rgba(79, 110, 247, 0.3);
}

.send-btn.active:hover {
  transform: scale(1.06);
  box-shadow: 0 8px 20px rgba(79, 110, 247, 0.36);
}

.send-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

/* 底部提示文字 */
.input-footer {
  display: flex;
  justify-content: space-between;
  max-width: 820px;
  margin: 8px auto 0;
  font-size: 11px;
  color: #b3b9c5;
  pointer-events: none;
  padding: 0 6px;
}

/* ===== 响应式 ===== */
/* 手机端空间太窄，收起缩略条（切换会话仍走左侧边栏） */
@media (max-width: 768px) {
  .conv-rail {
    display: none;
  }

  .chat-container.with-rail .messages-wrapper {
    padding-right: 0;
  }

  .chat-container.with-rail .input-wrapper {
    right: 10px;
  }
}

@media (max-width: 768px) {
  .menu-btn {
    display: block;
  }

  .messages-container {
    padding: 0 16px 120px;
  }

  .message-wrapper {
    margin-bottom: 20px;
  }

  .message-user .message-content {
    max-width: 82%;
  }

  .avatar {
    width: 30px;
    height: 30px;
    border-radius: 9px;
    font-size: 15px;
  }

  .avatar-user {
    font-size: 12px;
  }

  .input-wrapper {
    padding: 0 12px 10px;
  }

  .input-container {
    border-radius: 14px;
    padding: 6px 10px 6px 12px;
  }

  .quick-examples {
    flex-direction: column;
    align-items: stretch;
  }

  .welcome-title {
    font-size: 22px;
  }

  .chat-title {
    font-size: 14px;
  }

  .input-footer {
    display: none;
  }
}

@media (max-width: 480px) {
  .messages-wrapper {
    padding: 12px 0 100px;
  }

  .input-wrapper {
    padding: 0 8px 10px;
  }

  .input-container {
    border-radius: 12px;
    padding: 4px 8px 4px 12px;
  }

  .chat-input {
    font-size: 13px;
    padding: 8px 0;
    min-height: 38px;
  }
}

/* ===== 紧凑的思考链 ===== */
.thinking-chain {
  margin-bottom: 10px;
  background: #f8f9fb;
  border-radius: 10px;
  border: 1px solid #eef0f6;
  overflow: hidden;
  font-size: 13px;
}

.thinking-chain-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  cursor: pointer;
  user-select: none;
  transition: background 0.2s ease;
}

.thinking-chain-header:hover {
  background: #f1f3f5;
}

.chain-icon {
  font-size: 14px;
}

.chain-title {
  font-weight: 500;
  font-size: 12px;
  color: #495057;
  flex: 1;
}

.chain-status {
  font-size: 11px;
}

.chain-status .status-running {
  color: #4f6ef7;
  animation: pulseText 1.5s ease-in-out infinite;
}

.chain-status .status-done {
  color: #22c55e;
}

.chain-toggle {
  font-size: 14px;
  color: #868e96;
  font-weight: 300;
  line-height: 1;
}

/* ===== 思考链主体 ===== */
.thinking-chain-body {
  padding: 4px 12px 8px;
  border-top: 1px solid #e9ecef;
}

.chain-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  position: relative;
}

/* ===== 连接线 ===== */
.step-connector {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 14px;
  flex-shrink: 0;
  position: relative;
}

.connector-line {
  width: 2px;
  height: 16px;
  background: #dee2e6;
  transition: background 0.3s ease;
}

.connector-line.active {
  background: #4f6ef7;
}

.chain-step:last-child .connector-line {
  display: none;
}

.connector-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  border: 2px solid #dee2e6;
  background: #fff;
  transition: all 0.3s ease;
  margin: 1px 0;
}

/* 运行中的点 */
.connector-dot.running {
  border-color: #4f6ef7;
  background: #4f6ef7;
  animation: pulseDot 1s ease-in-out infinite;
}

/* 完成的点 */
.connector-dot.done {
  border-color: #22c55e;
  background: #22c55e;
}

/* 错误的点 */
.connector-dot.error {
  border-color: #ef4444;
  background: #ef4444;
}

/* 等待中的点 */
.connector-dot.pending {
  border-color: #dee2e6;
  background: #fff;
}

/* ===== 步骤信息 ===== */
.step-info {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
}

.step-name {
  font-size: 12px;
  color: #212529;
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.step-status-text {
  font-size: 12px;
  flex-shrink: 0;
}

/* 运行中的步骤高亮 */
.chain-step.running .step-name {
  color: #4f6ef7;
  font-weight: 500;
}

/* 完成的步骤 */
.chain-step.done .step-name {
  color: #495057;
}

/* 错误的步骤 */
.chain-step.error .step-name {
  color: #ef4444;
}

/* ===== 动画 ===== */
@keyframes pulseDot {
  0%, 100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.3);
    opacity: 0.7;
  }
}

@keyframes pulseText {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}
</style>