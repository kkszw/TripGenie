<!-- frontend/src/components/common/AppDialog.vue -->
<!-- 全应用统一的确认 / 输入 / 提示弹窗，视觉与登录窗保持一致 -->
<template>
  <Teleport to="body">
    <Transition name="dialog-fade">
      <div
          v-if="state.visible"
          class="dialog-overlay"
          @mousedown.self="cancelDialog"
      >
        <div
            ref="cardRef"
            class="dialog-card"
            role="dialog"
            aria-modal="true"
            tabindex="-1"
            :aria-label="state.title"
        >
          <!-- 顶部图标 -->
          <div class="dialog-icon" :class="`is-${state.variant}`">
            <svg
                v-if="state.icon === 'trash'"
                width="22" height="22" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"
            >
              <path d="M3 6h18M8 6V4.5A1.5 1.5 0 0 1 9.5 3h5A1.5 1.5 0 0 1 16 4.5V6"/>
              <path d="M18.5 6l-.8 13.1A1.9 1.9 0 0 1 15.8 21H8.2a1.9 1.9 0 0 1-1.9-1.9L5.5 6"/>
              <path d="M10 10.5v6M14 10.5v6"/>
            </svg>
            <svg
                v-else-if="state.icon === 'logout'"
                width="22" height="22" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"
            >
              <path d="M15 17l5-5-5-5"/>
              <path d="M20 12H9"/>
              <path d="M12 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h6"/>
            </svg>
            <svg
                v-else-if="state.icon === 'edit'"
                width="22" height="22" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"
            >
              <path d="M12 20h9"/>
              <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>
            </svg>
            <svg
                v-else-if="state.icon === 'warning'"
                width="22" height="22" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"
            >
              <path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>
              <path d="M12 9v4M12 17h.01"/>
            </svg>
            <svg
                v-else
                width="22" height="22" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"
            >
              <circle cx="12" cy="12" r="9.5"/>
              <path d="M12 11v6M12 7.6h.01"/>
            </svg>
          </div>

          <h3 class="dialog-title">{{ state.title }}</h3>
          <p v-if="state.message" class="dialog-message">{{ state.message }}</p>

          <!-- 输入框（仅 prompt 模式） -->
          <div v-if="state.mode === 'prompt'" class="dialog-field">
            <div class="field-head">
              <span v-if="state.inputLabel" class="field-label">{{ state.inputLabel }}</span>
              <span v-if="state.showCounter" class="field-counter">
                {{ state.value.length }}/{{ state.maxLength }}
              </span>
            </div>
            <input
                ref="inputRef"
                v-model="state.value"
                type="text"
                :placeholder="state.placeholder"
                :maxlength="state.maxLength"
                :class="{ 'has-error': !!state.error }"
                @input="clearDialogError"
                @keydown.enter.prevent="submitDialog"
            />
            <p v-if="state.error" class="field-error">{{ state.error }}</p>
          </div>

          <div class="dialog-actions">
            <button
                v-if="state.mode !== 'alert'"
                type="button"
                class="dialog-btn ghost"
                @click="cancelDialog"
            >
              {{ state.cancelText }}
            </button>
            <button
                type="button"
                class="dialog-btn solid"
                :class="`is-${state.variant}`"
                @click="submitDialog"
            >
              {{ state.confirmText }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import {nextTick, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import {
  cancelDialog,
  clearDialogError,
  dialogState as state,
  submitDialog
} from '@/composables/useDialog'

const inputRef = ref(null)
const cardRef = ref(null)

// 打开时把焦点放到位：输入框优先，否则落在卡片上（不落在按钮上，避免弹出瞬间出现焦点描边）
watch(() => state.visible, async (visible) => {
  if (!visible) return
  await nextTick()
  if (state.mode === 'prompt') {
    inputRef.value?.focus()
    inputRef.value?.select()
  } else {
    cardRef.value?.focus()
  }
})

const onKeydown = (e) => {
  if (!state.visible) return

  if (e.key === 'Escape') {
    e.preventDefault()
    cancelDialog()
    return
  }

  // 焦点在按钮上时交给按钮自身处理，避免 Enter 触发两次
  if (e.key === 'Enter' && e.target?.tagName !== 'BUTTON') {
    e.preventDefault()
    submitDialog()
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(17, 24, 39, 0.45);
  backdrop-filter: blur(6px);
}

.dialog-card {
  width: 100%;
  max-width: 384px;
  padding: 26px 24px 20px;
  background: #ffffff;
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18);
  text-align: center;
}

.dialog-card:focus {
  outline: none;
}

@keyframes dialog-pop {
  from {
    opacity: 0;
    transform: translateY(12px) scale(0.97);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* 图标 */
.dialog-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 46px;
  margin: 0 auto 14px;
  border-radius: 14px;
  background: #eef2ff;
  color: #4f6ef7;
}

.dialog-icon.is-danger {
  background: #fef2f2;
  color: #ef4444;
}

.dialog-icon.is-info {
  background: #f3f4f6;
  color: #6b7280;
}

/* 文案 */
.dialog-title {
  font-size: 16px;
  font-weight: 600;
  line-height: 1.4;
  color: #1a1a1a;
}

.dialog-message {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.65;
  color: #6b7280;
  word-break: break-word;
}

/* 输入区 */
.dialog-field {
  margin-top: 18px;
  text-align: left;
}

.field-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.field-label {
  font-size: 12px;
  color: #6b7280;
}

.field-counter {
  font-size: 11px;
  color: #b0b7c3;
  font-variant-numeric: tabular-nums;
}

.dialog-field input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  font-size: 14px;
  font-family: inherit;
  color: #1a1a1a;
  background: #ffffff;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.dialog-field input:focus {
  border-color: #4f6ef7;
  box-shadow: 0 0 0 3px rgba(79, 110, 247, 0.12);
}

.dialog-field input.has-error {
  border-color: #f04438;
}

.dialog-field input.has-error:focus {
  box-shadow: 0 0 0 3px rgba(240, 68, 56, 0.12);
}

.field-error {
  margin-top: 6px;
  font-size: 12px;
  color: #b42318;
}

/* 按钮 */
.dialog-actions {
  display: flex;
  gap: 10px;
  margin-top: 22px;
}

.dialog-btn {
  flex: 1;
  padding: 10px 16px;
  border: none;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.15s ease, transform 0.1s ease;
}

.dialog-btn:active {
  transform: scale(0.985);
}

.dialog-btn.ghost {
  background: #f3f4f6;
  color: #4b5563;
}

.dialog-btn.ghost:hover {
  background: #e5e7eb;
}

.dialog-btn.solid {
  background: #4f6ef7;
  color: #ffffff;
}

.dialog-btn.solid:hover {
  background: #3b5de7;
}

.dialog-btn.solid.is-danger {
  background: #ef4444;
}

.dialog-btn.solid.is-danger:hover {
  background: #dc2626;
}

.dialog-btn.solid.is-info {
  background: #4f6ef7;
}

.dialog-btn:focus-visible {
  outline: 2px solid rgba(79, 110, 247, 0.45);
  outline-offset: 2px;
}

/* 进出场 */
.dialog-fade-enter-active,
.dialog-fade-leave-active {
  transition: opacity 0.18s ease;
}

.dialog-fade-enter-from,
.dialog-fade-leave-to {
  opacity: 0;
}

.dialog-fade-enter-active .dialog-card {
  animation: dialog-pop 0.22s ease;
}

@media (max-width: 480px) {
  .dialog-card {
    max-width: 100%;
    padding: 22px 18px 16px;
  }
}
</style>
