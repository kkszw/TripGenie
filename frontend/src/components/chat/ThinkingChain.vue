<template>
  <div class="thinking-chain">
    <div class="chain-header" @click="expanded = !expanded">
      <span class="chain-toggle">{{ expanded ? '▼' : '▶' }}</span>
      <span class="chain-title">💭 思考过程</span>
      <span class="chain-status" :class="{ running: isStreaming }">
        {{ isStreaming ? '思考中...' : `${steps.length} 步` }}
      </span>
    </div>

    <div v-show="expanded" class="chain-steps">
      <div
        v-for="(step, index) in steps"
        :key="index"
        class="chain-step"
        :class="step.status"
      >
        <div class="step-indicator">
          <span v-if="step.status === 'running'" class="spinner"></span>
          <span v-else-if="step.status === 'done'">✅</span>
          <span v-else-if="step.status === 'error'">❌</span>
          <span v-else>⏸</span>
        </div>
        <div class="step-content">
          <div class="step-title">{{ step.node }}</div>
          <div class="step-desc">{{ step.message }}</div>
          <div v-if="step.detail" class="step-detail">
            <pre>{{ step.detail }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  steps: {
    type: Array,
    required: true,
    default: () => []
  },
  isStreaming: {
    type: Boolean,
    default: false
  }
})

const expanded = ref(true)
</script>

<style scoped>
.thinking-chain {
  margin-top: 12px;
  background: #ffffff;
  border-radius: 12px;
  border: 1px solid #e5e7eb;
  overflow: hidden;
  transition: all 0.3s ease;
}

.chain-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
  transition: all 0.2s ease;
  font-size: 13px;
  color: #6b7280;
}

.chain-header:hover {
  background: #f7f8fa;
}

.chain-toggle {
  font-size: 10px;
  color: #9ca3af;
  transition: transform 0.2s ease;
}

.chain-title {
  flex: 1;
  font-weight: 500;
}

.chain-status {
  font-size: 12px;
  color: #9ca3af;
  padding: 2px 10px;
  border-radius: 12px;
  background: #f7f8fa;
}

.chain-status.running {
  color: #4f6ef7;
  background: #eef2ff;
  animation: pulse 1.4s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.chain-steps {
  border-top: 1px solid #e5e7eb;
  padding: 8px 0;
  max-height: 400px;
  overflow-y: auto;
}

.chain-step {
  display: flex;
  gap: 12px;
  padding: 10px 14px;
  border-left: 3px solid transparent;
  transition: all 0.3s ease;
}

.chain-step.running {
  background: #eef2ff;
  border-left-color: #4f6ef7;
}

.chain-step.done {
  border-left-color: #10b981;
  opacity: 0.8;
}

.chain-step.error {
  border-left-color: #ef4444;
  background: #fef2f2;
}

.step-indicator {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 14px;
}

.spinner {
  width: 16px;
  height: 16px;
  border: 2px solid #e5e7eb;
  border-top-color: #4f6ef7;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.step-title {
  font-weight: 500;
  font-size: 13px;
  color: #1a1a1a;
}

.step-desc {
  font-size: 12px;
  color: #6b7280;
  margin-top: 2px;
}

.step-detail {
  margin-top: 6px;
  font-size: 12px;
  color: #6b7280;
}

.step-detail pre {
  background: #f7f8fa;
  padding: 8px 12px;
  border-radius: 6px;
  overflow-x: auto;
  font-family: 'SF Mono', 'Consolas', monospace;
  font-size: 12px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>