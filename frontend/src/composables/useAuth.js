// frontend/src/composables/useAuth.js
// 模块级单例：登录态必须全局只有一份。
// （上次 useSessions 被实例化两份导致侧边栏不刷新的坑，这里不重犯。）
import {computed, ref} from 'vue'
import {api} from '@/utils/api'
import {clearToken, getToken, setToken} from '@/utils/token'

const user = ref(null)
// initialized：首次校验本地令牌是否有效这件事有没有做完
const initialized = ref(false)
const showLogin = ref(false)
const busy = ref(false)
const error = ref('')

const isLoggedIn = computed(() => !!user.value)

const openLogin = () => {
    error.value = ''
    showLogin.value = true
}

const closeLogin = () => {
    showLogin.value = false
}

const applyAuth = (data) => {
    setToken(data.access_token)
    user.value = data.user
    showLogin.value = false
    error.value = ''
    return data.user
}

// 首次进入 / 刷新页面时调用：有令牌就换用户信息，没有就直接弹窗
const initAuth = async () => {
    if (initialized.value) return user.value
    if (!getToken()) {
        initialized.value = true
        openLogin()
        return null
    }
    try {
        user.value = await api.me()
    } catch (err) {
        // 令牌过期或被改过：api.me 已清掉本地令牌并广播 auth:expired。
        // 这里不调 openLogin()，否则会把上面那个"登录已过期"的提示一起清掉。
        clearToken()
        user.value = null
        error.value = '登录状态已失效，请重新登录'
        showLogin.value = true
    } finally {
        initialized.value = true
    }
    return user.value
}

const login = async (username, password) => {
    busy.value = true
    error.value = ''
    try {
        return applyAuth(await api.login(username, password))
    } catch (err) {
        error.value = err.message || '登录失败'
        throw err
    } finally {
        busy.value = false
    }
}

const register = async (username, password) => {
    busy.value = true
    error.value = ''
    try {
        return applyAuth(await api.register(username, password))
    } catch (err) {
        error.value = err.message || '注册失败'
        throw err
    } finally {
        busy.value = false
    }
}

const logout = () => {
    clearToken()
    user.value = null
    initialized.value = true
    openLogin()
}

// 任意接口返回 401 时（api.js 广播）统一回落到登录窗
if (typeof window !== 'undefined') {
    window.addEventListener('auth:expired', () => {
        if (!user.value && showLogin.value) return   // 登录窗已经开着，不重复处理
        user.value = null
        error.value = '登录已过期，请重新登录'
        showLogin.value = true
    })
}

export function useAuth() {
    return {
        user,
        isLoggedIn,
        initialized,
        showLogin,
        busy,
        error,
        initAuth,
        login,
        register,
        logout,
        openLogin,
        closeLogin
    }
}
