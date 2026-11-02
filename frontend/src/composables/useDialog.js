// frontend/src/composables/useDialog.js
// 统一的应用内对话框：替代浏览器原生 alert / confirm / prompt。
// 用法（任意组件内，无需挂载组件）：
//   const ok   = await confirmDialog({ title: '删除这个对话？', ... })
//   const name = await promptDialog({ title: '重命名对话', value: '旧名字' })
//   await alertDialog({ variant: 'danger', title: '删除失败', message: '...' })
// 真正的渲染由 App.vue 里的 <AppDialog /> 负责（全应用只挂一次）。
import {reactive} from 'vue'

// 默认值：三种模式各自有一套，open() 时按 mode 取默认再被调用方覆盖
const modeDefaults = {
    confirm: {title: '请确认', confirmText: '确定', cancelText: '取消', variant: 'primary', icon: 'info'},
    prompt: {title: '请输入', confirmText: '保存', cancelText: '取消', variant: 'primary', icon: 'edit'},
    alert: {title: '提示', confirmText: '知道了', cancelText: '取消', variant: 'info', icon: 'info'}
}

export const dialogState = reactive({
    visible: false,
    mode: 'alert',
    variant: 'info',
    icon: 'info',
    title: '',
    message: '',
    inputLabel: '',
    placeholder: '',
    maxLength: 40,
    value: '',
    error: '',
    required: true,
    requiredMessage: '内容不能为空',
    confirmText: '确定',
    cancelText: '取消',
    dismissible: true,
    showCounter: true
})

let resolver = null
let lastFocused = null

// 弹窗被"取消"时该返回什么：prompt 返回 null，其余返回 false
const cancelValue = () => (dialogState.mode === 'prompt' ? null : false)

const settle = (result) => {
    if (!resolver) return
    const resolve = resolver
    resolver = null
    dialogState.visible = false
    dialogState.error = ''
    resolve(result)

    // 关闭后把焦点还给触发它的按钮，键盘用户不会"丢失位置"
    const target = lastFocused
    lastFocused = null
    if (target && typeof target.focus === 'function') {
        try {
            target.focus({preventScroll: true})
        } catch (e) {
            /* 元素已被移除，忽略 */
        }
    }
}

const open = (options, mode) => new Promise((resolve) => {
    // 万一上一个弹窗还开着就把 Promise 永远悬着，这里先按"取消"结掉
    if (resolver) {
        const prev = resolver
        resolver = null
        prev(dialogState.mode === 'prompt' ? null : false)
    }

    const opts = typeof options === 'string' ? {message: options} : (options || {})

    Object.assign(dialogState, {
        // 先用一份干净的基础值，避免上一个弹窗的 placeholder/error 残留
        inputLabel: '',
        placeholder: '',
        maxLength: 40,
        value: '',
        error: '',
        required: true,
        requiredMessage: '内容不能为空',
        dismissible: true,
        showCounter: true,
        message: '',
        ...modeDefaults[mode],
        ...opts,
        mode,
        visible: true
    })

    lastFocused = document.activeElement
    resolver = resolve
})

// ===== 对外 API =====
export const confirmDialog = (options) => open(options, 'confirm')
export const promptDialog = (options) => open(options, 'prompt')
export const alertDialog = (options) => open(options, 'alert')

// ===== 给 AppDialog 组件用 =====
export const cancelDialog = () => {
    if (!dialogState.visible || !dialogState.dismissible) return
    settle(cancelValue())
}

export const submitDialog = () => {
    if (!dialogState.visible) return

    if (dialogState.mode === 'prompt') {
        const value = dialogState.value.trim()
        if (dialogState.required && !value) {
            dialogState.error = dialogState.requiredMessage
            return
        }
        if (dialogState.maxLength && value.length > dialogState.maxLength) {
            dialogState.error = `不能超过 ${dialogState.maxLength} 个字符`
            return
        }
        settle(value)
        return
    }

    settle(dialogState.mode === 'alert' ? undefined : true)
}

export const clearDialogError = () => {
    if (dialogState.error) dialogState.error = ''
}
