<!-- frontend/src/components/auth/LoginModal.vue -->
<template>
  <Teleport to="body">
    <Transition name="login-fade">
      <div v-if="showLogin" class="login-overlay">
        <div class="login-card">
          <div class="login-brand">
            <span class="brand-icon">🧳</span>
            <span class="brand-text">TripGenie</span>
          </div>
          <p class="login-subtitle">
            {{ mode === 'login' ? '登录后继续你的旅行规划' : '创建账号，保存专属行程' }}
          </p>

          <div class="login-tabs">
            <button
                type="button"
                class="login-tab"
                :class="{ active: mode === 'login' }"
                @click="switchMode('login')"
            >
              登录
            </button>
            <button
                type="button"
                class="login-tab"
                :class="{ active: mode === 'register' }"
                @click="switchMode('register')"
            >
              注册
            </button>
          </div>

          <form class="login-form" @submit.prevent="submit">
            <label class="login-field">
              <span class="field-label">用户名</span>
              <input
                  ref="usernameInput"
                  v-model.trim="username"
                  type="text"
                  autocomplete="username"
                  placeholder="2-32 个字符"
                  :disabled="busy"
              />
            </label>

            <label class="login-field">
              <span class="field-label">密码</span>
              <input
                  v-model="password"
                  type="password"
                  :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
                  placeholder="至少 6 位"
                  :disabled="busy"
              />
            </label>

            <label v-if="mode === 'register'" class="login-field">
              <span class="field-label">确认密码</span>
              <input
                  v-model="confirmPassword"
                  type="password"
                  autocomplete="new-password"
                  placeholder="再输入一次"
                  :disabled="busy"
              />
            </label>

            <p v-if="displayError" class="login-error">{{ displayError }}</p>

            <button class="login-submit" type="submit" :disabled="busy || !canSubmit">
              <span v-if="busy" class="submit-spinner"></span>
              {{ busy ? '请稍候...' : (mode === 'login' ? '登录' : '注册并开始') }}
            </button>
          </form>

          <p class="login-hint">
            登录后，你的对话记录只有自己能看到
          </p>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import {computed, nextTick, ref, watch} from 'vue'
import {useAuth} from '@/composables/useAuth'

const emit = defineEmits(['success'])

const {showLogin, busy, error, login, register} = useAuth()

const mode = ref('login')
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const localError = ref('')
const usernameInput = ref(null)

// 后端返回的错误优先，其次是本地校验
const displayError = computed(() => localError.value || error.value)

const canSubmit = computed(() => {
  if (!username.value || !password.value) return false
  if (mode.value === 'register' && !confirmPassword.value) return false
  return true
})

const switchMode = (next) => {
  if (mode.value === next) return
  mode.value = next
  localError.value = ''
  password.value = ''
  confirmPassword.value = ''
}

const validate = () => {
  if (username.value.length < 2 || username.value.length > 32) {
    localError.value = '用户名需要 2-32 个字符'
    return false
  }
  if (password.value.length < 6) {
    localError.value = '密码至少 6 位'
    return false
  }
  if (mode.value === 'register' && password.value !== confirmPassword.value) {
    localError.value = '两次输入的密码不一致'
    return false
  }
  return true
}

const submit = async () => {
  if (busy.value) return
  localError.value = ''
  if (!validate()) return

  try {
    const user = mode.value === 'login'
        ? await login(username.value, password.value)
        : await register(username.value, password.value)

    // 成功后清空表单，避免下次打开残留密码
    username.value = ''
    password.value = ''
    confirmPassword.value = ''
    emit('success', user)
  } catch (e) {
    // 错误文案已由 useAuth 写入 error，这里不再重复
  }
}

// 每次打开时聚焦用户名，并清掉上一次的错误
watch(showLogin, async (visible) => {
  if (!visible) return
  localError.value = ''
  await nextTick()
  usernameInput.value?.focus()
})
</script>

<style scoped>
.login-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(17, 24, 39, 0.45);
  backdrop-filter: blur(6px);
}

.login-card {
  width: 100%;
  max-width: 380px;
  padding: 32px 28px 24px;
  background: #ffffff;
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18);
  animation: login-pop 0.22s ease;
}

@keyframes login-pop {
  from {
    opacity: 0;
    transform: translateY(12px) scale(0.97);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.login-brand {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 20px;
  font-weight: 600;
  color: #1a1a1a;
}

.brand-icon {
  font-size: 26px;
}

.login-subtitle {
  margin-top: 6px;
  text-align: center;
  font-size: 13px;
  color: #9ca3af;
}

.login-tabs {
  display: flex;
  gap: 4px;
  margin: 20px 0 18px;
  padding: 3px;
  background: #f3f4f6;
  border-radius: 10px;
}

.login-tab {
  flex: 1;
  padding: 8px 0;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-size: 14px;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.18s ease;
}

.login-tab.active {
  background: #ffffff;
  color: #4f6ef7;
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.login-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 12px;
  color: #6b7280;
}

.login-field input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  font-size: 14px;
  color: #1a1a1a;
  background: #ffffff;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.login-field input:focus {
  border-color: #4f6ef7;
  box-shadow: 0 0 0 3px rgba(79, 110, 247, 0.12);
}

.login-field input:disabled {
  background: #f9fafb;
  color: #9ca3af;
}

.login-error {
  margin: -4px 0 0;
  padding: 8px 10px;
  font-size: 12.5px;
  line-height: 1.45;
  color: #b42318;
  background: #fef3f2;
  border: 1px solid #fecdca;
  border-radius: 8px;
}

.login-submit {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 4px;
  padding: 11px 0;
  border: none;
  border-radius: 10px;
  background: #4f6ef7;
  color: #ffffff;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease;
}

.login-submit:hover:not(:disabled) {
  background: #3b5de7;
}

.login-submit:disabled {
  background: #c7d2fe;
  cursor: not-allowed;
}

.submit-spinner {
  width: 13px;
  height: 13px;
  border: 2px solid rgba(255, 255, 255, 0.45);
  border-top-color: #ffffff;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.login-hint {
  margin-top: 16px;
  text-align: center;
  font-size: 11.5px;
  color: #b0b7c3;
}

.login-fade-enter-active,
.login-fade-leave-active {
  transition: opacity 0.18s ease;
}

.login-fade-enter-from,
.login-fade-leave-to {
  opacity: 0;
}
</style>
