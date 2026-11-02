// frontend/src/utils/token.js
// 令牌的唯一存放点。api.js 与 useAuth.js 都从这里读写，避免出现"两处各存一份"的状态不一致。
const TOKEN_KEY = 'tripgenie_token'

export const getToken = () => {
    try {
        return localStorage.getItem(TOKEN_KEY) || ''
    } catch (e) {
        // 隐私模式下 localStorage 可能不可用
        return ''
    }
}

export const setToken = (token) => {
    try {
        if (token) localStorage.setItem(TOKEN_KEY, token)
        else localStorage.removeItem(TOKEN_KEY)
    } catch (e) {
        console.warn('令牌写入失败:', e)
    }
}

export const clearToken = () => setToken('')
