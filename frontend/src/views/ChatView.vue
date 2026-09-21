<!-- frontend/src/views/ChatView.vue -->
<template>
  <div class="chat-container">
    <!-- 顶部标题栏 -->
    <header class="chat-header">
      <div class="header-left">
        <button class="menu-btn" @click="$emit('toggle-sidebar')" title="切换侧边栏">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 12H21M3 6H21M3 18H21" stroke-linecap="round"/>
          </svg>
        </button>
        <span class="chat-title">{{ currentTitle }}</span>
      </div>
      <div class="header-right">
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
                @click="sendMessage(example.text)"
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
              <div class="message-bubble bubble-ai">
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

          <!-- 用户消息 -->
          <div v-else class="message-user fade-in-up">
            <div class="message-content">
              <div class="message-bubble bubble-user">
                {{ msg.content }}
              </div>
              <div class="message-time">
                {{ formatTime(msg.timestamp) }}
              </div>
            </div>
            <div class="avatar avatar-user">👤</div>
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

        <!-- 中断弹窗（HITL） -->
        <div v-if="showInterruptModal" class="interrupt-modal-overlay">
          <div class="interrupt-modal">
            <h3>需要你的确认</h3>
            <div class="interrupt-content">
              <p>{{ interruptMessage }}</p>
              <div v-if="interruptDetails" class="interrupt-details">
                <pre>{{ JSON.stringify(interruptDetails, null, 2) }}</pre>
              </div>
            </div>
            <div class="interrupt-actions">
              <button class="btn-cancel" @click="closeInterruptModal">取消</button>
              <button class="btn-approve" @click="approveInterrupt">✅ 批准</button>
              <button class="btn-reject" @click="rejectInterrupt">❌ 拒绝</button>
            </div>
          </div>
        </div>

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

  </div>
</template>

<script setup>
import {ref, computed, nextTick, onMounted, watch, onUnmounted} from 'vue'
import {api} from '@/utils/api'
import {useSessions} from "@/composables/useSessions.js";
import TripPlanCard from '@/components/chat/TripPlanCard.vue'
import TripPlanModal from '@/components/chat/TripPlanModal.vue'

const props = defineProps({
  threadId: {
    type: String,
    default: null
  }
})

const emit = defineEmits(['new-chat', 'update-title', 'toggle-sidebar', 'thread-created'])

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
// 中断相关
const showInterruptModal = ref(false)
const interruptMessage = ref('')
const interruptDetails = ref(null)
let pendingInterrupt = null

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

    case 'interrupt':
      if (isLoading.value) {
        isLoading.value = false
      }
      pendingInterrupt = data.interrupt
      showInterruptModal.value = true
      interruptMessage.value = data.interrupt?.message || '需要你的确认'
      interruptDetails.value = data.interrupt
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

// ===== 发送消息 =====
const handleSend = async () => {
  const text = inputText.value.trim()
  if (!text || isLoading.value) return

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
        const data = JSON.parse(jsonMatch[1])
        return convertToTripPlan(data)
      } catch (e) {
        console.warn('⚠️ JSON 代码块解析失败:', e)
      }
    }

    // 方法2: 查找 JSON 代码块 (``` ... ```) 不带 json 标记
    jsonMatch = content.match(/```\s*([\s\S]*?)\s*```/)
    if (jsonMatch) {
      try {
        const data = JSON.parse(jsonMatch[1])
        return convertToTripPlan(data)
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
          const data = JSON.parse(jsonStr)
          return convertToTripPlan(data)
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

// ===== 中断决策 =====
const closeInterruptModal = () => {
  showInterruptModal.value = false
  pendingInterrupt = null
}

const approveInterrupt = () => {
  closeInterruptModal()
  sendInterruptDecision({type: 'approve'})
}

const rejectInterrupt = () => {
  closeInterruptModal()
  sendInterruptDecision({type: 'reject', message: '用户拒绝了操作'})
}

const sendInterruptDecision = async (decision) => {
  const threadId = props.threadId || currentThreadId.value
  if (!threadId) return

  isLoading.value = true

  const aiMessage = {
    role: 'assistant',
    content: '',
    timestamp: new Date().toISOString()
  }
  messages.value.push(aiMessage)

  try {
    const response = await api.sendMessage(threadId, '', decision)

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
            handleSSEEvent(data, aiMessage)
            const lastMsg = messages.value[messages.value.length - 1]
            if (lastMsg && lastMsg.role === 'assistant') {
              lastMsg.content = aiMessage.content
            }
            await loadSessions()
            scrollToBottom()
          } catch (e) {
            // 忽略非 JSON 行
          }
        }
      }
    }
  } catch (error) {
    console.error('❌ 中断决策失败:', error)
    aiMessage.content = `处理中断决策失败: ${error.message}`
    isLoading.value = false
  }
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
})
// ===== 在组件卸载时清理定时器 =====
onUnmounted(() => {
  stopDisplayTimer()
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
}

/* ===== 头部 ===== */
.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-bottom: 1px solid #e5e7eb;
  flex-shrink: 0;
  background: #ffffff;
  z-index: 5;
  min-height: 50px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
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
  font-weight: 500;
  color: #1a1a1a;
}

.header-btn {
  background: none;
  border: none;
  padding: 6px 10px;
  cursor: pointer;
  color: #6b7280;
  border-radius: 6px;
  transition: all 0.2s ease;
  font-size: 13px;
}

.header-btn:hover {
  background: #f7f8fa;
  color: #1a1a1a;
}

/* ===== 消息区域 ===== */
.messages-wrapper {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 16px 0 20px;
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
  max-width: 860px;
  margin: 0 auto;
  padding: 0 24px 140px;
}

/* ===== 欢迎界面 ===== */
.welcome-screen {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 400px;
  text-align: center;
  padding: 40px 20px;
}

.welcome-icon {
  font-size: 64px;
  margin-bottom: 24px;
  animation: bounce 2s ease-in-out infinite;
}

.welcome-title {
  font-size: 28px;
  font-weight: 600;
  margin-bottom: 12px;
  background: linear-gradient(135deg, #4f6ef7, #7c3aed);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.welcome-subtitle {
  font-size: 15px;
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
  gap: 8px;
  padding: 10px 18px;
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  color: #1a1a1a;
  cursor: pointer;
  transition: all 0.2s ease;
  font-size: 13px;
}

.example-btn:hover {
  border-color: #4f6ef7;
  box-shadow: 0 4px 12px rgba(79, 110, 247, 0.12);
  transform: translateY(-2px);
}

.example-icon {
  font-size: 16px;
}

/* ===== 消息样式 ===== */
.message-wrapper {
  display: flex;
  margin-bottom: 20px;
}

.message-ai {
  display: flex;
  gap: 14px;
  width: 100%;
}

.message-user {
  display: flex;
  gap: 14px;
  width: 100%;
  justify-content: flex-end;
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}

.avatar-ai {
  background: #eef2ff;
}

.avatar-user {
  background: #e5e7eb;
}

.message-content {
  flex: 1;
  max-width: 85%;
}

.message-text {
  word-wrap: break-word;
  overflow-wrap: break-word;
}

.message-bubble {
  padding: 14px 18px;
  border-radius: 18px;
  font-size: 14px;
  line-height: 1.7;
}

.bubble-ai {
  background: #f0f0f0;
  color: #1a1a1a;
  border-bottom-left-radius: 4px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}

.bubble-user {
  background: #4f6ef7;
  color: white;
  border-bottom-right-radius: 4px;
}

.message-time {
  font-size: 11px;
  color: #9ca3af;
  margin-top: 4px;
  text-align: right;
  padding-right: 4px;
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
  background: linear-gradient(to top, #ffffff 80%, transparent);
  z-index: 10;
  pointer-events: none;
}

.input-container {
  max-width: 860px;
  margin: 0 auto;
  pointer-events: auto;
  background: #ffffff;
  border-radius: 16px;
  border: 1.5px solid #e5e7eb;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06);
  transition: all 0.25s ease;
  padding: 6px 12px 6px 16px;
}

.input-container:focus-within {
  border-color: #4f6ef7;
  box-shadow: 0 4px 24px rgba(79, 110, 247, 0.15), 0 0 0 4px rgba(79, 110, 247, 0.06);
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
  padding: 8px 0;
  font-size: 15px;
  line-height: 1.6;
  resize: none;
  outline: none;
  font-family: inherit;
  color: #1a1a1a;
  max-height: 200px;
  min-height: 44px;
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
  padding: 4px 0;
  border-top: 1px solid #f3f4f6;
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
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  background: #e5e7eb;
  color: #9ca3af;
  cursor: pointer;
  transition: all 0.2s ease;
}

.send-btn.active {
  background: #4f6ef7;
  color: white;
}

.send-btn.active:hover {
  background: #3b5de7;
  transform: scale(1.05);
}

.send-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

/* 底部提示文字 */
.input-footer {
  display: flex;
  justify-content: space-between;
  max-width: 860px;
  margin: 8px auto 0;
  font-size: 11px;
  color: #9ca3af;
  pointer-events: none;
  padding: 0 4px;
}

/* ===== 中断弹窗 ===== */
.interrupt-modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.interrupt-modal {
  background: #ffffff;
  border-radius: 16px;
  padding: 24px;
  max-width: 500px;
  width: 90%;
  max-height: 80vh;
  overflow-y: auto;
}

.interrupt-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
}

.interrupt-actions button {
  padding: 8px 16px;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
}

.btn-cancel {
  background: #e5e7eb;
  color: #1a1a1a;
}

.btn-approve {
  background: #22c55e;
  color: white;
}

.btn-reject {
  background: #ef4444;
  color: white;
}

/* ===== 响应式 ===== */
@media (max-width: 768px) {
  .menu-btn {
    display: block;
  }

  .messages-container {
    padding: 0 16px 120px;
  }

  .message-content {
    max-width: 92%;
  }

  .input-wrapper {
    padding: 0 12px 10px;
  }

  .input-container {
    padding: 4px 10px 4px 12px;
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
  background: #f8f9fa;
  border-radius: 8px;
  border: 1px solid #e9ecef;
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