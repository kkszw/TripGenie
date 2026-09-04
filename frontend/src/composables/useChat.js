import { ref, reactive } from 'vue'

export function useChat() {
  const messages = ref([])
  const isLoading = ref(false)
  const isStreaming = ref(false)

  const context = reactive({
    destination: '',
    days: 3,
    budget: 5000,
    preferences: []
  })

  const sendMessage = async (text, options = {}) => {
    const userMessage = {
      role: 'user',
      content: text,
      timestamp: Date.now()
    }
    messages.value.push(userMessage)

    isLoading.value = true
    isStreaming.value = true

    const aiMessage = {
      role: 'assistant',
      content: '',
      thinkingSteps: [],
      isStreaming: true,
      timestamp: Date.now()
    }
    messages.value.push(aiMessage)

    try {
      await simulateThinking(aiMessage, text)
      await generateResponse(aiMessage, text, options)

      const plan = extractTripPlan(text)
      if (plan) {
        aiMessage.tripPlan = plan
      }

    } catch (error) {
      console.error('发送消息失败:', error)
      aiMessage.content = '抱歉，我遇到了一些问题，请稍后重试。'
    } finally {
      aiMessage.isStreaming = false
      isLoading.value = false
      isStreaming.value = false
    }

    return aiMessage
  }

  const simulateThinking = async (aiMessage, userInput) => {
    const steps = [
      {
        node: '理解需求',
        message: '分析您的旅行偏好和需求...',
        detail: `用户想要去 ${extractDestination(userInput) || '某个地方'} 旅行`
      },
      {
        node: '检索攻略',
        message: '从知识库中搜索相关游记和攻略...',
        detail: '找到 5 篇类似的旅行经验'
      },
      {
        node: '规划行程',
        message: '根据预算和偏好生成行程方案...',
        detail: `预算: ${extractBudget(userInput) || '待定'} 元`
      },
      {
        node: '优化建议',
        message: '调整行程顺序和时间安排...',
        detail: '已优化景点间的交通路线'
      }
    ]

    for (let i = 0; i < steps.length; i++) {
      const step = { ...steps[i], status: 'running' }
      aiMessage.thinkingSteps = [...steps.slice(0, i), step]
      await sleep(600 + Math.random() * 400)
      step.status = 'done'
      aiMessage.thinkingSteps = [...steps.slice(0, i + 1).map(s => ({ ...s, status: 'done' }))]
      await sleep(300)
    }
  }

  const generateResponse = async (aiMessage, userInput, options) => {
    const dest = extractDestination(userInput) || '目的地'
    const days = extractDays(userInput) || 3
    const budget = extractBudget(userInput) || 5000

    const responseTemplates = {
      default: `根据您的需求，我为您规划了以下 ${days} 天 ${dest} 行程：

📅 **Day 1**: 抵达${dest}，入住市中心酒店，晚上逛当地夜市，品尝特色小吃

📅 **Day 2**: 上午游览${dest}必去景点，下午体验当地传统文化活动

📅 **Day 3**: 前往${dest}周边小众景点，深度感受自然风光，傍晚返程

💡 **预算参考**: 住宿 ¥${Math.round(budget * 0.4)}，交通 ¥${Math.round(budget * 0.3)}，餐饮 ¥${Math.round(budget * 0.2)}，门票及其他 ¥${Math.round(budget * 0.1)}

⭐ **行程亮点**: 特色美食、文化体验、绝美风光

这是初步方案，您可以根据喜好调整！`,

      food: `🍜 作为美食爱好者，我为您精心设计了 ${dest} 美食之旅：

📅 **Day 1**: 抵达后直奔${dest}最著名的美食街，打卡当地小吃

📅 **Day 2**: 探访${dest}老字号餐厅，品尝正宗地方菜

📅 **Day 3**: 参加当地烹饪课程，学习制作特色美食

💡 推荐必吃：${dest}特色菜、夜市小吃、地道早餐`,

      nature: `🌿 为您规划了 ${dest} 自然风光之旅：

📅 **Day 1**: 抵达后前往${dest}最美观景台，看日落

📅 **Day 2**: 徒步${dest}国家公园，感受大自然的鬼斧神工

📅 **Day 3**: 清晨观日出，游览${dest}周边的古镇

⭐ 建议携带：舒适的运动鞋、防晒用品、相机`
    }

    let template = responseTemplates.default
    if (userInput.includes('美食') || userInput.includes('吃')) {
      template = responseTemplates.food
    } else if (userInput.includes('自然') || userInput.includes('风景') || userInput.includes('徒步')) {
      template = responseTemplates.nature
    }

    for (let i = 0; i < template.length; i++) {
      aiMessage.content += template[i]
      await sleep(15 + Math.random() * 15)
    }
  }

  const extractDestination = (text) => {
    const destinations = ['三亚', '成都', '大理', '北京', '上海', '杭州', '丽江', '厦门', '青岛', '西安', '重庆', '长沙']
    for (const dest of destinations) {
      if (text.includes(dest)) return dest
    }
    return null
  }

  const extractDays = (text) => {
    const match = text.match(/(\d+)\s*天/)
    return match ? parseInt(match[1]) : null
  }

  const extractBudget = (text) => {
    const match = text.match(/(\d+)\s*元/)
    return match ? parseInt(match[1]) : null
  }

  // ✅ 修复后的 extractTripPlan
  const extractTripPlan = (text) => {
    const dest = extractDestination(text)
    if (!dest) return null

    const totalDays = extractDays(text) || 3  // 改名避免重复
    const budget = extractBudget(text) || 5000

    const dayPlans = []
    for (let i = 0; i < totalDays; i++) {
      dayPlans.push({
        day: i + 1,
        attractions: [
          { name: `${dest}${i === 0 ? '市中心' : i === 1 ? '主要景区' : '周边景点'}` }
        ],
        meals: [
          i === 0 ? '当地特色晚餐' : i === 1 ? '地道午餐' : '美食探索',
          '早餐：酒店早餐'
        ],
        hotel: i === 0 ? '市中心精品酒店' : '景区附近酒店'
      })
    }

    return {
      id: Date.now(),
      destination: dest,
      totalDays: totalDays,  // 总天数
      budget: budget,
      startDate: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
      rating: (4 + Math.random() * 0.8).toFixed(1),
      days: dayPlans,  // 每天的详细行程
      highlights: ['特色美食', '文化深度体验', '舒适住宿', '合理路线']
    }
  }

  const clearChat = () => {
    messages.value = []
  }

  const regenerate = async (index) => {
    const userMsgIndex = messages.value.findIndex((m, i) => i < index && m.role === 'user')
    if (userMsgIndex === -1) return
    const userMsg = messages.value[userMsgIndex]
    messages.value.splice(index, 1)
    await sendMessage(userMsg.content)
  }

  const copyMessage = (content) => {
    navigator.clipboard?.writeText(content).then(() => {
      console.log('已复制到剪贴板')
    })
  }

  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms))

  return {
    messages,
    isLoading,
    isStreaming,
    context,
    sendMessage,
    clearChat,
    regenerate,
    copyMessage
  }
}