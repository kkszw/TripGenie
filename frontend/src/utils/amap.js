/**
 * 高德地图 JS API 2.0 加载器
 *
 * 为什么不用 index.html 里直接写 <script>：
 * 1. JS API 2.0 从 2021-12-02 起申请的 Key 必须搭配「安全密钥」使用，
 *    且安全密钥必须在脚本加载「之前」写入 window._AMapSecurityConfig；
 * 2. 脚本加载失败（Key 被回收 / 域名白名单 / 网络）时，<script> 标签不会给出任何反馈，
 *    页面只会留下一个灰块。这里改成 Promise，能把失败原因抛给调用方并展示出来。
 */

const KEY = import.meta.env.VITE_AMAP_JS_KEY
const SECURITY_CODE = import.meta.env.VITE_AMAP_SECURITY_CODE

let loadingPromise = null

export function loadAMap() {
  // 已经加载过（或热更新后仍在页面上）
  if (typeof window !== 'undefined' && window.AMap) {
    return Promise.resolve(window.AMap)
  }
  if (loadingPromise) return loadingPromise

  loadingPromise = new Promise((resolve, reject) => {
    if (!KEY) {
      reject(new Error('未配置 VITE_AMAP_JS_KEY，请检查 frontend/.env'))
      return
    }

    // 安全密钥必须在脚本加载前设置，否则 JS API 2.0 会报 INVALID_USER_SCODE
    if (SECURITY_CODE) {
      window._AMapSecurityConfig = { securityJsCode: SECURITY_CODE }
    }

    const script = document.createElement('script')
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${KEY}`
    script.async = true

    script.onload = () => {
      if (window.AMap) {
        resolve(window.AMap)
      } else {
        reject(new Error('脚本已加载但 AMap 未初始化：Key 无效、已被回收，或类型不是「Web端(JS API)」'))
      }
    }
    script.onerror = () => {
      reject(new Error('地图脚本请求失败：检查网络，或 Key 的域名白名单是否包含当前地址'))
    }

    document.head.appendChild(script)
  }).catch((err) => {
    // 失败后允许下次重试（例如用户刚改了 Key 触发热更新）
    loadingPromise = null
    throw err
  })

  return loadingPromise
}
