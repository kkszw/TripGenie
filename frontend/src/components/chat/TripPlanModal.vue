<!-- components/chat/TripPlanModal.vue -->
<template>
  <Teleport to="body">
    <Transition name="modal-fade">
      <div v-if="visible" class="modal-overlay" @click.self="close">
        <div class="modal-container">
          <!-- 关闭按钮 -->
          <button class="modal-close" @click="close">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M18 6L6 18M6 6L18 18" stroke-linecap="round"/>
            </svg>
          </button>

          <!-- 标题区 -->
          <div class="modal-header">
            <div class="modal-title">
              <span class="modal-icon">🌍</span>
              <span>{{ plan.destination }} {{ plan.totalDays }}日游</span>
            </div>
            <div class="modal-meta">
              <span class="modal-budget">💰 ¥{{ plan.budget }}</span>
              <span class="modal-date">📅 {{ plan.startDate || '待定' }}</span>
            </div>
          </div>

          <!-- 导航提示 -->
          <div class="modal-nav-hint">
            <span class="nav-arrow">←</span>
            左右滑动查看每日行程
            <span class="nav-arrow">→</span>
          </div>

          <!-- 滑动容器 -->
          <div class="modal-carousel-wrapper">
            <div class="modal-carousel" :style="carouselStyle" @touchstart="onTouchStart" @touchmove="onTouchMove"
                 @touchend="onTouchEnd">
              <div v-for="(day, index) in plan.days" :key="index" class="modal-day-card">
                <!-- ✅ 内层滚动容器 -->
                <div class="modal-day-scroll">
                  <div class="modal-day-header">
                    <div class="modal-day-info">
                      <span class="modal-day-number">Day {{ day.day || index + 1 }}</span>
                      <span v-if="day.date" class="modal-day-date">{{ day.date }}</span>
                    </div>
                    <span class="modal-day-theme">{{ day.theme || `第 ${day.day || index + 1} 天` }}</span>
                    <span v-if="day.estimatedCost" class="modal-day-cost">
                      预估 ¥{{ day.estimatedCost }}
                    </span>
                  </div>

                  <!-- 每日天气 -->
                  <div v-if="day.weather" class="modal-day-weather">
                    <span class="weather-icon">🌤️</span>
                    <span class="weather-text">{{ day.weather }}</span>
                  </div>

                  <!-- 活动列表 -->
                  <div class="modal-day-activities">
                    <div v-for="(act, actIndex) in day.attractions" :key="actIndex" class="modal-activity-item">
                      <span v-if="act.time" class="modal-activity-time">{{ act.time }}</span>
                      <div class="modal-activity-detail">
                        <span class="modal-activity-name">{{ act.name }}</span>
                        <span v-if="act.description" class="modal-activity-desc">{{ act.description }}</span>
                      </div>
                      <span v-if="act.cost" class="modal-activity-cost">¥{{ act.cost }}</span>
                    </div>
                  </div>

                  <!-- 住宿 -->
                  <div v-if="day.hotel && day.hotel !== '待定'" class="modal-day-hotel">
                    🏨 {{ day.hotel }}
                  </div>

                  <!-- 备注 -->
                  <div v-if="day.notes" class="modal-day-notes">
                    📝 {{ day.notes }}
                  </div>

                  <!-- ✅ 当日地图（放在行程描述下方） -->
                  <DayMap
                      v-if="currentIndex === index && getDayAttractions(day.day || index + 1).length > 0"
                      :key="`day-map-${index}-${currentIndex}`"
                      :day="day.day || index + 1"
                      :attractions="getDayAttractions(day.day || index + 1)"
                      :routes="getDayRoutes(day.day || index + 1)"
                      :center="plan.map_data?.center"
                  />

                  <!-- 页码 -->
                  <div class="modal-day-page">
                    {{ index + 1 }} / {{ plan.days.length }}
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 底部指示器 -->
          <div class="modal-dots">
            <span
                v-for="(_, index) in totalPages"
                :key="index"
                class="modal-dot"
                :class="{ active: currentIndex === index }"
                @click="goToDay(index)"
            ></span>
          </div>

          <!-- 导航按钮 -->
          <div class="modal-nav-buttons">
            <button class="modal-nav-btn prev" @click="prevDay" :disabled="currentIndex === 0">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M15 18L9 12L15 6" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </button>
            <button class="modal-nav-btn next" @click="nextDay" :disabled="currentIndex === totalPages - 1">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M9 18L15 12L9 6" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </button>
          </div>

          <!-- 底部 -->
          <div class="modal-footer">
            <span class="modal-footer-hint">← 左右滑动查看全部 {{ totalPages }} 页</span>
            <button class="modal-btn-close" @click="close">关闭</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import {ref, computed, watch, onMounted, onUnmounted} from 'vue'
import DayMap from './DayMap.vue'  // ✅ 导入 DayMap

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  plan: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['update:visible', 'close'])

// ===== 状态 =====
const currentIndex = ref(0)
const touchStartX = ref(0)
const touchCurrentX = ref(0)
const isDragging = ref(false)

// ===== 计算 =====
// 现在总页数 = 天数（不再有单独的地图页）
const totalPages = computed(() => props.plan.days.length)

const carouselStyle = computed(() => {
  const offset = -currentIndex.value * 100
  return {
    transform: `translateX(${offset}%)`,
    transition: isDragging.value ? 'none' : 'transform 0.4s cubic-bezier(0.25, 0.46, 0.45, 0.94)'
  }
})

// ✅ 获取某天的景点
const getDayAttractions = (dayNum) => {
  if (!props.plan.map_data || !props.plan.map_data.attractions) return []
  return props.plan.map_data.attractions.filter(a => a.day === dayNum)
}

// ✅ 获取某天的路线（起点在当天的景点）
const getDayRoutes = (dayNum) => {
  if (!props.plan.map_data || !props.plan.map_data.routes) return []
  const dayAttractions = getDayAttractions(dayNum)
  const dayNames = dayAttractions.map(a => a.name)

  return props.plan.map_data.routes.filter(route => {
    return dayNames.includes(route.from)
  })
}

// ===== 方法 =====
const close = () => {
  emit('update:visible', false)
  emit('close')
}

const goToDay = (index) => {
  if (index >= 0 && index < totalPages.value) {
    currentIndex.value = index
  }
}

const nextDay = () => {
  if (currentIndex.value < totalPages.value - 1) {
    currentIndex.value++
  }
}

const prevDay = () => {
  if (currentIndex.value > 0) {
    currentIndex.value--
  }
}

// ===== 触摸事件 =====
const onTouchStart = (e) => {
  touchStartX.value = e.touches[0].clientX
  isDragging.value = true
}

const onTouchMove = (e) => {
  if (!isDragging.value) return
  touchCurrentX.value = e.touches[0].clientX
}

const onTouchEnd = () => {
  if (!isDragging.value) return
  isDragging.value = false
  const diff = touchStartX.value - touchCurrentX.value
  if (Math.abs(diff) > 50) {
    if (diff > 0) nextDay()
    else prevDay()
  }
}

// ===== 键盘事件 =====
const handleKeydown = (e) => {
  if (!props.visible) return
  if (e.key === 'Escape') close()
  else if (e.key === 'ArrowLeft') prevDay()
  else if (e.key === 'ArrowRight') nextDay()
}

// ===== 重置索引 =====
watch(() => props.visible, (newVal) => {
  if (newVal) {
    currentIndex.value = 0
    document.body.style.overflow = 'hidden'

    // ✅ 弹窗打开后，延迟触发 resize，让地图重新计算尺寸
    setTimeout(() => {
      window.dispatchEvent(new Event('resize'))
    }, 600)
  } else {
    document.body.style.overflow = ''
  }
})
watch(() => currentIndex.value, (newIndex) => {
  setTimeout(() => {
    window.dispatchEvent(new Event('resize'))
  }, 150)
})

// ===== 生命周期 =====
onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown)
  document.body.style.overflow = ''
})
</script>

<style scoped>
/* ===== 遮罩层 ===== */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  padding: 20px;
}

/* ===== 容器 ===== */
.modal-container {
  background: #ffffff;
  border-radius: 20px;
  max-width: 700px;
  width: 100%;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  position: relative;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
  overflow: hidden;
  animation: modalSlideUp 0.3s ease forwards;
}

@keyframes modalSlideUp {
  from {
    opacity: 0;
    transform: translateY(30px) scale(0.95);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* ===== 关闭按钮 ===== */
.modal-close {
  position: absolute;
  top: 12px;
  right: 12px;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.06);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #6b7280;
  transition: all 0.2s ease;
  z-index: 10;
}

.modal-close:hover {
  background: rgba(0, 0, 0, 0.12);
  transform: rotate(90deg);
}

/* ===== 头部 ===== */
.modal-header {
  padding: 20px 24px 16px;
  border-bottom: 1px solid #e5e7eb;
  flex-shrink: 0;
}

.modal-title {
  font-size: 20px;
  font-weight: 600;
  color: #1a1a2e;
  display: flex;
  align-items: center;
  gap: 10px;
}

.modal-icon {
  font-size: 24px;
}

.modal-meta {
  display: flex;
  gap: 16px;
  margin-top: 8px;
  font-size: 14px;
  color: #6b7280;
}

.modal-budget {
  color: #10b981;
  font-weight: 500;
}

/* ===== 导航提示 ===== */
.modal-nav-hint {
  padding: 8px 24px;
  text-align: center;
  font-size: 13px;
  color: #9ca3af;
  background: #f7f8fa;
  border-bottom: 1px solid #e5e7eb;
  flex-shrink: 0;
}

.nav-arrow {
  margin: 0 8px;
  color: #4f6ef7;
}

/* ===== 轮播容器 ===== */
.modal-carousel-wrapper {
  flex: 1;
  overflow: hidden;
  padding: 16px 0;
  min-height: 300px;
  position: relative;
}

.modal-carousel {
  display: flex;
  height: 100%;
  will-change: transform;
}

/* ===== 每日卡片 ===== */
.modal-day-card {
  flex: 0 0 100%;
  padding: 0 24px;
  overflow: hidden; /* 外卡片不滚动 */
  max-height: 55vh;
  height: 55vh; /* 固定高度让内层滚动 */
}

/* ✅ 内层滚动容器 */
.modal-day-scroll {
  height: 100%;
  overflow-y: auto;
  overflow-x: hidden;
  padding-right: 8px;
  padding-bottom: 16px;
  scrollbar-width: thin;
  scrollbar-color: rgba(0, 0, 0, 0.15) transparent;
}

.modal-day-scroll::-webkit-scrollbar {
  width: 6px;
}

.modal-day-scroll::-webkit-scrollbar-track {
  background: transparent;
  margin: 4px 0;
}

.modal-day-scroll::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.15);
  border-radius: 3px;
}

.modal-day-scroll::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 0, 0, 0.3);
}

.modal-day-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding-bottom: 10px;
  border-bottom: 2px solid #eef2ff;
  margin-bottom: 12px;
}

.modal-day-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.modal-day-number {
  font-size: 18px;
  font-weight: 700;
  color: #4f6ef7;
}

.modal-day-date {
  font-size: 13px;
  color: #9ca3af;
}

.modal-day-theme {
  font-size: 14px;
  color: #1a1a2e;
  font-weight: 500;
  flex: 1;
  text-align: center;
}

.modal-day-cost {
  font-size: 13px;
  color: #10b981;
  font-weight: 500;
  background: #ecfdf5;
  padding: 2px 10px;
  border-radius: 12px;
}

/* 每日天气 */
.modal-day-weather {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  margin-bottom: 12px;
  background: linear-gradient(135deg, #e8f4f8 0%, #d4e8f0 100%);
  border-radius: 8px;
  border-left: 3px solid #4a9bc7;
}

.modal-day-weather .weather-icon {
  font-size: 16px;
}

.modal-day-weather .weather-text {
  font-size: 13px;
  color: #1a4a5a;
}

/* 活动列表 */
.modal-day-activities {
  padding-left: 4px;
}

.modal-activity-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 6px 0;
  font-size: 13px;
  color: #4a5568;
  border-bottom: 1px dashed #f3f6fa;
}

.modal-activity-item:last-child {
  border-bottom: none;
}

.modal-activity-time {
  color: #9ca3af;
  font-size: 12px;
  min-width: 70px;
  flex-shrink: 0;
  padding-top: 1px;
}

.modal-activity-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.modal-activity-name {
  font-weight: 500;
  color: #1a1a2e;
}

.modal-activity-desc {
  font-size: 12px;
  color: #6b7280;
  padding-left: 8px;
  border-left: 2px solid #e5e7eb;
}

.modal-activity-cost {
  color: #10b981;
  font-size: 12px;
  flex-shrink: 0;
  padding-top: 1px;
  font-weight: 500;
}

/* 住宿 */
.modal-day-hotel {
  margin-top: 10px;
  padding: 6px 12px;
  background: #f7fafc;
  border-radius: 6px;
  font-size: 13px;
  color: #4a5568;
}

/* 备注 */
.modal-day-notes {
  margin-top: 8px;
  padding: 6px 12px;
  background: #fefce8;
  border-radius: 6px;
  font-size: 12px;
  color: #92400e;
  line-height: 1.5;
}

/* 页码 */
.modal-day-page {
  margin-top: 12px;
  text-align: center;
  font-size: 12px;
  color: #9ca3af;
}

/* ===== 底部指示器 ===== */
.modal-dots {
  display: flex;
  justify-content: center;
  gap: 8px;
  padding: 8px 0 12px;
  flex-shrink: 0;
}

.modal-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #d1d5db;
  cursor: pointer;
  transition: all 0.3s ease;
}

.modal-dot.active {
  background: #4f6ef7;
  width: 24px;
  border-radius: 4px;
}

.modal-dot:hover {
  background: #9ca3af;
}

.modal-dot.active:hover {
  background: #4f6ef7;
}

/* ===== 导航按钮 ===== */
.modal-nav-buttons {
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  transform: translateY(-50%);
  pointer-events: none;
  padding: 0 8px;
}

.modal-nav-btn {
  position: absolute;
  pointer-events: auto;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.15);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #4a5568;
  transition: all 0.2s ease;
}

.modal-nav-btn:hover:not(:disabled) {
  background: #4f6ef7;
  color: white;
  transform: scale(1.05);
}

.modal-nav-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.modal-nav-btn.prev {
  left: 8px;
}

.modal-nav-btn.next {
  right: 8px;
}

/* ===== 底部 ===== */
.modal-footer {
  padding: 12px 24px 20px;
  border-top: 1px solid #e5e7eb;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;
}

.modal-btn-close {
  padding: 8px 24px;
  border: none;
  border-radius: 10px;
  background: #4f6ef7;
  color: white;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
}

.modal-btn-close:hover {
  background: #3b5de7;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(79, 110, 247, 0.3);
}

.modal-footer-hint {
  font-size: 12px;
  color: #9ca3af;
}

/* ===== 动画 ===== */
.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: all 0.3s ease;
}

.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
  transform: scale(0.9);
}

/* ===== 响应式 ===== */
@media (max-width: 640px) {
  .modal-overlay {
    padding: 10px;
  }

  .modal-container {
    max-height: 95vh;
    border-radius: 16px;
  }

  .modal-header {
    padding: 16px 18px 12px;
  }

  .modal-title {
    font-size: 17px;
  }

  .modal-meta {
    font-size: 12px;
    gap: 10px;
  }

  .modal-day-card {
    padding: 0 16px;
    max-height: 60vh;
    height: 60vh;
  }

  .modal-day-number {
    font-size: 16px;
  }

  .modal-day-theme {
    font-size: 12px;
  }

  .modal-activity-item {
    font-size: 12px;
    padding: 4px 0;
  }

  .modal-activity-time {
    min-width: 55px;
    font-size: 11px;
  }

  .modal-nav-hint {
    font-size: 11px;
    padding: 4px 16px;
  }

  .modal-nav-btn {
    width: 28px;
    height: 28px;
  }

  .modal-nav-btn svg {
    width: 16px;
    height: 16px;
  }

  .modal-footer {
    padding: 10px 16px 16px;
    flex-direction: column;
    gap: 8px;
  }

  .modal-btn-close {
    width: 100%;
    padding: 10px;
  }

  .modal-footer-hint {
    font-size: 11px;
  }

  .modal-nav-buttons {
    display: none;
  }
}
</style>