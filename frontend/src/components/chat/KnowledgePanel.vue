<!-- components/chat/KnowledgePanel.vue
     用户资料库：上传 PDF、查看入库进度、删除。

     为什么是独立面板而不是塞进对话流：入库是几十秒级的异步过程（实测 14 页
     扫描版 OCR 要 51 秒），放在对话流里会占住屏幕，而且它和某一轮对话无关——
     传进来的资料是跨会话复用的资产。 -->
<template>
  <Teleport to="body">
    <Transition name="kb-fade">
      <div v-if="visible" class="kb-overlay" @click.self="close">
        <div class="kb-panel">
          <header class="kb-header">
            <div class="kb-title">
              <span class="kb-icon">📎</span>
              <div>
                <h2>我的资料</h2>
                <p>上传 PDF 攻略或行程单，规划时会参考它的内容</p>
              </div>
            </div>
            <button class="kb-close" title="关闭" @click="close">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" stroke-width="2">
                <path d="M18 6L6 18M6 6L18 18" stroke-linecap="round"/>
              </svg>
            </button>
          </header>

          <!-- 上传区：点击和拖拽都能用 -->
          <div
              class="kb-drop"
              :class="{ 'is-dragging': dragging, 'is-busy': uploading }"
              @click="pickFile"
              @dragover.prevent="dragging = true"
              @dragleave.prevent="dragging = false"
              @drop.prevent="onDrop"
          >
            <input
                ref="fileInput"
                type="file"
                accept=".pdf,application/pdf"
                hidden
                @change="onPick"
            />
            <div class="kb-drop-inner">
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" stroke-width="1.6">
                <path d="M12 16V4m0 0L8 8m4-4 4 4" stroke-linecap="round"
                      stroke-linejoin="round"/>
                <path d="M20 16v2a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-2" stroke-linecap="round"/>
              </svg>
              <p class="kb-drop-main">{{ uploading ? '正在上传…' : '点击选择或拖入 PDF' }}</p>
              <p class="kb-drop-hint">单份不超过 20MB，上传后自动解析入库</p>
            </div>
          </div>

          <p v-if="errorMessage" class="kb-error">{{ errorMessage }}</p>
          <p v-if="notice" class="kb-notice">{{ notice }}</p>

          <!-- 资料列表 -->
          <div class="kb-list-head">
            <span>已上传 {{ docs.length }} 份</span>
            <span v-if="totalChunks">共 {{ totalChunks }} 个知识块</span>
          </div>

          <div class="kb-list">
            <p v-if="!docs.length && !loading" class="kb-empty">
              还没有资料。传一份 PDF，规划时会用上它。
            </p>

            <article v-for="doc in docs" :key="doc.doc_id" class="kb-item">
              <div class="kb-item-top">
                <div class="kb-item-name" :title="doc.filename">
                  <span class="kb-file-icon">PDF</span>
                  <span class="kb-name-text">{{ doc.filename }}</span>
                </div>
                <button class="kb-delete" title="删除" @click.stop="removeDoc(doc)">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" stroke-width="1.8">
                    <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" stroke-linecap="round"
                          stroke-linejoin="round"/>
                  </svg>
                </button>
              </div>

              <!-- 入库进度：只有非终态才显示 -->
              <div v-if="isBusy(doc)" class="kb-progress">
                <div class="kb-progress-bar">
                  <span :style="{ width: Math.max(doc.progress, 4) + '%' }"></span>
                </div>
                <span class="kb-progress-text">{{ statusLabel(doc) }} {{ doc.progress }}%</span>
              </div>

              <div class="kb-item-meta">
                <span v-if="doc.status === 'ready'" class="kb-badge ready">
                  已入库 {{ doc.chunks }} 块
                </span>
                <span v-else-if="doc.status === 'failed'" class="kb-badge failed">入库失败</span>
                <span v-else class="kb-badge pending">{{ statusLabel(doc) }}</span>

                <span v-if="doc.pages">{{ doc.pages }} 页</span>
                <span v-if="doc.extract_mode === 'ocr'" class="kb-ocr" title="文字层损坏，已用 OCR 识别">OCR</span>
                <span v-if="doc.days">{{ doc.days }} 天</span>
                <span class="kb-size">{{ formatSize(doc.size_bytes) }}</span>
              </div>

              <p v-if="doc.error" class="kb-item-error">{{ doc.error }}</p>
            </article>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import {computed, onBeforeUnmount, ref, watch} from 'vue'
import {api} from '@/utils/api'

const props = defineProps({
  visible: {type: Boolean, default: false}
})
const emit = defineEmits(['close'])

const docs = ref([])
const totalChunks = ref(0)
const loading = ref(false)
const uploading = ref(false)
const dragging = ref(false)
const errorMessage = ref('')
const notice = ref('')
const fileInput = ref(null)

let pollTimer = null

// 这三个状态表示后台任务还在跑，需要继续轮询
const BUSY = ['pending', 'parsing', 'chunking', 'indexing']

const isBusy = (doc) => BUSY.includes(doc.status)

const STATUS_TEXT = {
  pending: '排队中',
  parsing: '解析中',
  chunking: '分块中',
  indexing: '建立索引',
  ready: '已入库',
  failed: '失败'
}
const statusLabel = (doc) => STATUS_TEXT[doc.status] || doc.status

const formatSize = (bytes) => {
  if (!bytes) return ''
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const load = async () => {
  loading.value = true
  try {
    const data = await api.getDocs()
    docs.value = data.docs || []
    totalChunks.value = data.total_chunks || 0
    errorMessage.value = ''
  } catch (e) {
    errorMessage.value = e.message || '加载资料失败'
  } finally {
    loading.value = false
  }
}

// 轮询而不是 WebSocket：入库最长一两分钟，2 秒一次的代价可以忽略，
// 却省掉了连接管理、断线重连、多标签页同步这一整套东西。
const schedulePoll = () => {
  stopPoll()
  if (!docs.value.some(isBusy)) return
  pollTimer = setTimeout(async () => {
    await load()
    schedulePoll()
  }, 2000)
}

const stopPoll = () => {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

const pickFile = () => {
  if (uploading.value) return
  fileInput.value?.click()
}

const onPick = (event) => {
  const file = event.target.files?.[0]
  // 清空 value，否则选中同一个文件不会再触发 change
  event.target.value = ''
  if (file) upload(file)
}

const onDrop = (event) => {
  dragging.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) upload(file)
}

const upload = async (file) => {
  errorMessage.value = ''
  notice.value = ''

  if (!file.name.toLowerCase().endsWith('.pdf')) {
    errorMessage.value = '只支持 PDF 文件'
    return
  }

  uploading.value = true
  try {
    const result = await api.uploadDoc(file)
    if (result.duplicated) {
      notice.value = `《${result.filename}》已经在资料库里了，没有重复入库`
    }
    await load()
    schedulePoll()
  } catch (e) {
    errorMessage.value = e.message || '上传失败'
  } finally {
    uploading.value = false
  }
}

const removeDoc = async (doc) => {
  if (!window.confirm(`删除《${doc.filename}》？删除后规划不再参考它。`)) return
  try {
    await api.deleteDoc(doc.doc_id)
    docs.value = docs.value.filter((item) => item.doc_id !== doc.doc_id)
    totalChunks.value = docs.value.reduce((sum, item) => sum + (item.chunks || 0), 0)
    notice.value = '已删除'
  } catch (e) {
    errorMessage.value = e.message || '删除失败'
  }
}

const close = () => emit('close')

watch(() => props.visible, (open) => {
  if (open) {
    errorMessage.value = ''
    notice.value = ''
    load().then(schedulePoll)
  } else {
    stopPoll()
  }
})

onBeforeUnmount(stopPoll)
</script>

<style scoped>
.kb-overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 24, 33, 0.32);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1200;
  padding: 24px;
}

.kb-panel {
  width: min(560px, 100%);
  max-height: min(720px, 86vh);
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 18px;
  box-shadow: 0 24px 60px rgba(20, 24, 33, 0.18);
  padding: 22px 22px 8px;
  overflow: hidden;
}

.kb-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.kb-title {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.kb-icon {
  font-size: 20px;
  line-height: 1.2;
}

.kb-title h2 {
  font-size: 17px;
  font-weight: 600;
  color: #1a1a1a;
}

.kb-title p {
  margin-top: 3px;
  font-size: 12.5px;
  color: #7b8394;
  line-height: 1.5;
}

.kb-close {
  flex: none;
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #7b8394;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.kb-close:hover {
  background: #f0f2f7;
  color: #1a1a1a;
}

.kb-drop {
  margin-top: 16px;
  border: 1.5px dashed #d3d9e6;
  border-radius: 14px;
  padding: 18px 12px;
  text-align: center;
  color: #6b7385;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.kb-drop:hover,
.kb-drop.is-dragging {
  border-color: #4f6ef7;
  background: #f6f8ff;
}

.kb-drop.is-busy {
  opacity: 0.6;
  cursor: progress;
}

.kb-drop-inner svg {
  color: #4f6ef7;
}

.kb-drop-main {
  margin-top: 6px;
  font-size: 13.5px;
  font-weight: 500;
  color: #2c3242;
}

.kb-drop-hint {
  margin-top: 3px;
  font-size: 12px;
  color: #98a0b0;
}

.kb-error,
.kb-notice {
  margin-top: 10px;
  font-size: 12.5px;
  line-height: 1.5;
  border-radius: 8px;
  padding: 8px 10px;
}

.kb-error {
  background: #fef2f2;
  color: #c0392b;
}

.kb-notice {
  background: #f1f5ff;
  color: #4f6ef7;
}

.kb-list-head {
  display: flex;
  justify-content: space-between;
  margin: 16px 2px 8px;
  font-size: 12px;
  color: #98a0b0;
}

.kb-list {
  flex: 1;
  min-height: 80px;
  overflow-y: auto;
  padding-bottom: 14px;
}

.kb-empty {
  padding: 26px 8px;
  text-align: center;
  font-size: 13px;
  color: #98a0b0;
}

.kb-item {
  border: 1px solid #eef0f5;
  border-radius: 12px;
  padding: 11px 12px;
  margin-bottom: 8px;
  background: #fcfdff;
}

.kb-item-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.kb-item-name {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.kb-file-icon {
  flex: none;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.4px;
  color: #fff;
  background: #e0554a;
  border-radius: 4px;
  padding: 2px 4px;
}

.kb-name-text {
  font-size: 13px;
  color: #2c3242;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kb-delete {
  flex: none;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: #b3bac8;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.kb-delete:hover {
  background: #feecec;
  color: #d64545;
}

.kb-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 9px;
}

.kb-progress-bar {
  flex: 1;
  height: 4px;
  border-radius: 2px;
  background: #eceff6;
  overflow: hidden;
}

.kb-progress-bar span {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: linear-gradient(90deg, #5b78ff, #7c3aed);
  transition: width 0.4s ease;
}

.kb-progress-text {
  flex: none;
  font-size: 11.5px;
  color: #7b8394;
  font-variant-numeric: tabular-nums;
}

.kb-item-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
  font-size: 11.5px;
  color: #98a0b0;
}

.kb-badge {
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 11px;
}

.kb-badge.ready {
  background: #edf7ee;
  color: #2e8b47;
}

.kb-badge.failed {
  background: #fef2f2;
  color: #c0392b;
}

.kb-badge.pending {
  background: #eef2ff;
  color: #4f6ef7;
}

.kb-ocr {
  border-radius: 4px;
  padding: 1px 5px;
  background: #fff6e5;
  color: #b7791f;
  font-size: 10.5px;
}

.kb-item-error {
  margin-top: 7px;
  font-size: 11.5px;
  line-height: 1.5;
  color: #c0392b;
}

.kb-fade-enter-active,
.kb-fade-leave-active {
  transition: opacity 0.18s ease;
}

.kb-fade-enter-from,
.kb-fade-leave-to {
  opacity: 0;
}
</style>
