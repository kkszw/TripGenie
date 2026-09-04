<template>
  <div class="input-wrapper">
    <div class="input-container" :class="{ 'is-focused': isFocused }">
      <textarea
        ref="textareaRef"
        v-model="inputText"
        class="chat-input"
        :placeholder="disabled ? 'AI 正在思考...' : placeholder"
        :disabled="disabled"
        rows="1"
        @keydown.enter.exact.prevent="handleEnter"
        @input="autoResize"
        @focus="isFocused = true"
        @blur="isFocused = false"
      ></textarea>

      <div class="input-actions">
        <!-- 快捷指令按钮 -->
        <button
          v-if="showQuickActions"
          class="input-btn"
          @click="$emit('toggle-quick-actions')"
          title="快捷指令"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M4 6H20M4 12H20M4 18H12" stroke-linecap="round"/>
          </svg>
        </button>

        <!-- 上传附件（可选） -->
        <button class="input-btn" @click="$emit('upload')" title="上传文件">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 16V4M8 8L12 4L16 8" stroke-linecap="round"/>
            <path d="M4 16V18C4 19.1046 4.89543 20 6 20H18C19.1046 20 20 19.1046 20 18V16" stroke-linecap="round"/>
          </svg>
        </button>

        <!-- 发送按钮 -->
        <button
          class="send-btn"
          :class="{ active: inputText.trim() }"
          @click="send"
          :disabled="!inputText.trim() || disabled"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M2 21L21 12L2 3V10L15 12L2 14V21Z"/>
          </svg>
        </button>
      </div>
    </div>

    <!-- 字符计数 -->
    <div v-if="showCounter" class="input-counter">
      {{ inputText.length }} / {{ maxLength }}
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, watch } from 'vue'

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: '描述你的旅行计划...'
  },
  disabled: {
    type: Boolean,
    default: false
  },
  showQuickActions: {
    type: Boolean,
    default: true
  },
  showCounter: {
    type: Boolean,
    default: false
  },
  maxLength: {
    type: Number,
    default: 2000
  },
  autoFocus: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits([
  'update:modelValue',
  'send',
  'toggle-quick-actions',
  'upload'
])

const inputText = ref(props.modelValue)
const textareaRef = ref(null)
const isFocused = ref(false)

watch(inputText, (newVal) => {
  emit('update:modelValue', newVal)
})

watch(() => props.modelValue, (newVal) => {
  if (newVal !== inputText.value) {
    inputText.value = newVal
  }
})

const handleEnter = (e) => {
  if (e.shiftKey) {
    // Shift+Enter 换行
    return
  }
  e.preventDefault()
  send()
}

const send = () => {
  const text = inputText.value.trim()
  if (!text || props.disabled) return
  emit('send', text)
  inputText.value = ''
  autoResize()
}

const autoResize = () => {
  const el = textareaRef.value
  if (el) {
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 150) + 'px'
  }
}

const focus = () => {
  textareaRef.value?.focus()
}

defineExpose({ focus, autoResize })

// 自动聚焦
if (props.autoFocus) {
  nextTick(() => focus())
}
</script>

<style scoped>
.input-wrapper {
  position: relative;
  padding: 16px 24px 24px;
  border-top: 1px solid #e5e7eb;
  background: #ffffff;
  flex-shrink: 0;
}

.input-container {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  background: #f7f8fa;
  border-radius: 20px;
  padding: 8px 12px;
  border: 2px solid transparent;
  transition: all 0.2s ease;
}

.input-container.is-focused {
  border-color: #4f6ef7;
  background: #ffffff;
  box-shadow: 0 0 0 4px rgba(79, 110, 247, 0.1);
}

.chat-input {
  flex: 1;
  border: none;
  background: transparent;
  padding: 8px 0;
  font-size: 15px;
  line-height: 1.6;
  resize: none;
  outline: none;
  font-family: inherit;
  color: #1a1a1a;
  max-height: 150px;
  min-height: 24px;
}

.chat-input::placeholder {
  color: #9ca3af;
}

.chat-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.input-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.input-btn {
  background: none;
  border: none;
  padding: 6px;
  color: #9ca3af;
  cursor: pointer;
  border-radius: 8px;
  transition: all 0.2s ease;
}

.input-btn:hover {
  background: #e5e7eb;
  color: #1a1a1a;
}

.send-btn {
  background: #e5e7eb;
  border: none;
  padding: 8px 12px;
  border-radius: 8px;
  color: #9ca3af;
  cursor: pointer;
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  justify-content: center;
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

.input-counter {
  text-align: right;
  font-size: 12px;
  color: #9ca3af;
  margin-top: 4px;
  padding-right: 4px;
}

@media (max-width: 640px) {
  .input-wrapper {
    padding: 12px 16px 16px;
  }
}
</style>