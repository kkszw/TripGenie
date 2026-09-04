<!-- components/chat/TripPlanCard.vue -->
<template>
  <div class="trip-plan-card">
    <!-- 标题区 -->
    <div class="plan-header" @click="$emit('expand', plan)">
      <div class="plan-title">
        <span class="plan-icon">🌍</span>
        <span>{{ plan.destination }} {{ plan.totalDays }}日游</span>
      </div>
      <div class="plan-meta">
        <span class="plan-budget">💰 ¥{{ plan.budget }}</span>
        <span class="plan-date">📅 {{ plan.startDate || '待定' }}</span>
      </div>
    </div>

    <!-- 精简信息 -->
    <div class="plan-preview">
      <div class="plan-preview-item">
        <span class="preview-label">📅 天数</span>
        <span class="preview-value">{{ plan.totalDays }} 天</span>
      </div>
      <div class="plan-preview-item">
        <span class="preview-label">📍 目的地</span>
        <span class="preview-value">{{ plan.destination }}</span>
      </div>
      <div class="plan-preview-item">
        <span class="preview-label">💰 预算</span>
        <span class="preview-value">¥{{ plan.budget }}</span>
      </div>
    </div>

    <!-- 每日行程概览（折叠显示） -->
    <div class="plan-days-preview">
      <div v-for="(day, index) in plan.days.slice(0, 2)" :key="index" class="day-preview-item">
        <span class="day-preview-number">Day {{ day.day || index + 1 }}</span>
        <span class="day-preview-theme">{{ day.theme || `第 ${day.day || index + 1} 天` }}</span>
        <span class="day-preview-count">{{ day.attractions?.length || 0 }} 个活动</span>
      </div>
      <div v-if="plan.days.length > 2" class="day-preview-more">
        还有 {{ plan.days.length - 2 }} 天行程...
      </div>
    </div>

    <!-- 操作按钮 - 点击展开完整行程 -->
    <div class="plan-actions">
      <button class="btn-expand" @click="$emit('expand', plan)">
        👆 点击查看完整行程
      </button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  plan: {
    type: Object,
    required: true
  }
})

defineEmits(['expand'])
</script>

<style scoped>
.trip-plan-card {
  background: linear-gradient(135deg, #f8f9ff 0%, #f0f4ff 100%);
  border-radius: 14px;
  padding: 16px 18px;
  margin: 12px 0;
  border: 1px solid #e8edf5;
  box-shadow: 0 2px 8px rgba(79, 110, 247, 0.08);
  cursor: pointer;
  transition: all 0.3s ease;
}

.trip-plan-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(79, 110, 247, 0.15);
  border-color: #4f6ef7;
}

.plan-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding-bottom: 10px;
  border-bottom: 1px dashed #dce3ef;
}

.plan-title {
  font-size: 16px;
  font-weight: 600;
  color: #1a1a2e;
  display: flex;
  align-items: center;
  gap: 8px;
}

.plan-icon {
  font-size: 20px;
}

.plan-meta {
  display: flex;
  gap: 12px;
  font-size: 13px;
  color: #4a5568;
}

.plan-budget {
  color: #10b981;
  font-weight: 500;
}

/* 精简信息 */
.plan-preview {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  padding: 10px 0;
  border-bottom: 1px dashed #e8edf5;
}

.plan-preview-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4px 8px;
  background: rgba(255, 255, 255, 0.6);
  border-radius: 8px;
}

.preview-label {
  font-size: 11px;
  color: #9ca3af;
}

.preview-value {
  font-size: 14px;
  font-weight: 500;
  color: #1a1a2e;
  margin-top: 2px;
}

/* 行程概览 */
.plan-days-preview {
  padding: 8px 0;
}

.day-preview-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 0;
  font-size: 13px;
  color: #4a5568;
}

.day-preview-item:not(:last-child) {
  border-bottom: 1px dashed #f3f6fa;
}

.day-preview-number {
  color: #4f6ef7;
  font-weight: 600;
  min-width: 50px;
}

.day-preview-theme {
  flex: 1;
  color: #1a1a2e;
}

.day-preview-count {
  font-size: 12px;
  color: #9ca3af;
}

.day-preview-more {
  text-align: center;
  font-size: 13px;
  color: #9ca3af;
  padding: 4px 0;
  font-style: italic;
}

/* 操作按钮 */
.plan-actions {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e8edf5;
  text-align: center;
}

.btn-expand {
  padding: 8px 24px;
  background: linear-gradient(135deg, #4f6ef7, #7c3aed);
  color: white;
  border: none;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s ease;
  width: 100%;
}

.btn-expand:hover {
  transform: scale(1.02);
  box-shadow: 0 4px 16px rgba(79, 110, 247, 0.35);
}

/* 响应式 */
@media (max-width: 640px) {
  .trip-plan-card {
    padding: 14px 16px;
  }

  .plan-title {
    font-size: 14px;
  }

  .plan-meta {
    font-size: 12px;
    gap: 8px;
  }

  .plan-preview {
    grid-template-columns: repeat(3, 1fr);
    gap: 4px;
  }

  .preview-value {
    font-size: 12px;
  }

  .day-preview-item {
    font-size: 12px;
  }

  .btn-expand {
    font-size: 13px;
    padding: 6px 16px;
  }
}
</style>