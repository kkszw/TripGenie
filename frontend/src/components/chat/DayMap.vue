<template>
  <div class="day-map-wrapper">
    <div class="day-map-header">
      <span class="day-map-icon">🗺️</span>
      <span class="day-map-title">第 {{ day }} 天路线地图</span>
      <span class="day-map-count">{{ attractions.length }} 个景点</span>
    </div>
    <div
        :id="mapId"
        class="day-map-container"
    ></div>
    <div v-if="routes && routes.length > 0" class="day-map-routes">
      <div
          v-for="(route, idx) in routes"
          :key="idx"
          class="route-item"
      >
        <span class="route-index" :style="{ background: getColor(idx) }">{{ idx + 1 }}</span>
        <span class="route-text">{{ route.from }} → {{ route.to }}</span>
        <span class="route-distance">{{ formatDistance(route.distance) }}</span>
        <span class="route-duration">{{ formatDuration(route.duration) }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import {ref, onMounted, onUnmounted, watch, nextTick, computed} from 'vue'

const props = defineProps({
  day: {type: Number, required: true},
  attractions: {type: Array, default: () => []},
  routes: {type: Array, default: () => []},
  center: {type: Array, default: null}
})

const isValidCoord = (coord) =>
    Array.isArray(coord) && coord.length >= 2 &&
    Number.isFinite(Number(coord[0])) && Number.isFinite(Number(coord[1]))

const mapId = computed(() => `day-map-${props.day}-${Math.random().toString(36).slice(2, 8)}`)
let mapInstance = null
let retryTimer = null
let resizeTimer = null

const colors = ['#4f6ef7', '#ef4444', '#22c55e', '#f59e0b', '#8b5cf6']
const getColor = (idx) => colors[idx % colors.length]

const formatDistance = (meters) => {
  if (!meters) return ''
  if (meters < 1000) return `${Math.round(meters)}m`
  return `${(meters / 1000).toFixed(1)}km`
}

const formatDuration = (seconds) => {
  if (!seconds) return ''
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}分钟`
  const hours = Math.floor(minutes / 60)
  const mins = minutes % 60
  return mins > 0 ? `${hours}小时${mins}分` : `${hours}小时`
}

const initMap = (retryCount = 0) => {
  if (!props.attractions || props.attractions.length === 0) return

  nextTick(() => {
    const container = document.getElementById(mapId.value)
    if (!container) return

    const w = container.offsetWidth
    const h = container.offsetHeight
    if (w === 0 || h === 0) {
      if (retryCount < 15) {
        retryTimer = setTimeout(() => initMap(retryCount + 1), 300)
      }
      return
    }

    if (typeof window === 'undefined' || !window.AMap) {
      const checkAMap = setInterval(() => {
        if (window.AMap) {
          clearInterval(checkAMap)
          initMap(retryCount)
        }
      }, 500)
      setTimeout(() => clearInterval(checkAMap), 10000)
      return
    }

    try {
      if (mapInstance) {
        mapInstance.destroy()
        mapInstance = null
      }
      console.log('📍 attractions:', props.attractions.map(a => a.name))
      console.log('🚗 routes:', props.routes.map(r => `${r.from}→${r.to}`))
      // ✅ 过滤有效坐标
      const validAttractions = props.attractions.filter(a => {
        const valid = a &&
            typeof a.lng === 'number' && !isNaN(a.lng) && isFinite(a.lng) &&
            typeof a.lat === 'number' && !isNaN(a.lat) && isFinite(a.lat)
        if (!valid) console.warn(`❌ 无效坐标: ${a?.name} lng=${a?.lng} lat=${a?.lat}`)
        return valid
      })

      console.log(`📍 有效景点: ${validAttractions.length}/${props.attractions.length}`)

      // 计算 center：优先当日景点的包围盒中心，其次后端下发的城市中心
      let centerLng = null
      let centerLat = null
      if (validAttractions.length > 0) {
        const lngs = validAttractions.map(a => Number(a.lng))
        const lats = validAttractions.map(a => Number(a.lat))
        centerLng = (Math.min(...lngs) + Math.max(...lngs)) / 2
        centerLat = (Math.min(...lats) + Math.max(...lats)) / 2
      } else if (isValidCoord(props.center)) {
        centerLng = Number(props.center[0])
        centerLat = Number(props.center[1])
      }

      if (centerLng === null || centerLat === null) {
        console.warn('❌ 无可用坐标，跳过地图初始化')
        return
      }

      // ✅ 创建地图
      const map = new window.AMap.Map(mapId.value, {
        zoom: 12,
        center: [centerLng, centerLat],
        mapStyle: 'amap://styles/whitesmoke',
        showIndoorMap: false,
        resizeEnable: true
      })

      // ✅ marker 循环必须在 nextTick 回调里，这样能访问到 map
      validAttractions.forEach((attr, index) => {
        try {
          const marker = new window.AMap.Marker({
            position: [Number(attr.lng), Number(attr.lat)],
            title: attr.name,
            label: {
              content: `<div style="font-size:11px;padding:3px 8px;background:#4f6ef7;color:white;border-radius:10px;font-weight:bold;white-space:nowrap;">${index + 1}. ${attr.name}</div>`,
              direction: 'top'
            }
          })
          marker.setMap(map)   // ✅ 能访问到 map
        } catch (e) {
          console.warn(`❌ marker 失败: ${attr.name}`, e)
        }
      })

      // ✅ polyline 循环也在回调里
      if (props.routes && props.routes.length > 0) {
        props.routes.forEach((route, idx) => {
          try {
            let validPath = null
            if (route.polyline && Array.isArray(route.polyline) && route.polyline.length > 0) {
              validPath = route.polyline
                  .filter(p => Array.isArray(p) && p.length >= 2)
                  .map(p => [Number(p[0]), Number(p[1])])
                  .filter(p => !isNaN(p[0]) && !isNaN(p[1]) && isFinite(p[0]) && isFinite(p[1]))
            }

            if (validPath && validPath.length >= 2) {
              const polyline = new window.AMap.Polyline({
                path: validPath,
                strokeColor: getColor(idx),
                strokeWeight: 5,
                strokeOpacity: 0.8,
                showDir: true
              })
              polyline.setMap(map)   // ✅ 能访问到 map
            } else if (route.from_coord && route.to_coord) {
              const fc = [Number(route.from_coord[0]), Number(route.from_coord[1])]
              const tc = [Number(route.to_coord[0]), Number(route.to_coord[1])]
              const validFrom = !isNaN(fc[0]) && !isNaN(fc[1]) && isFinite(fc[0]) && isFinite(fc[1])
              const validTo = !isNaN(tc[0]) && !isNaN(tc[1]) && isFinite(tc[0]) && isFinite(tc[1])

              if (validFrom && validTo) {
                const polyline = new window.AMap.Polyline({
                  path: [fc, tc],
                  strokeColor: getColor(idx),
                  strokeWeight: 3,
                  strokeStyle: 'dashed',
                  strokeOpacity: 0.7
                })
                polyline.setMap(map)
              }
            }
          } catch (e) {
            console.warn(`❌ 添加路线失败: ${route.from} → ${route.to}`, e)
          }
        })
      }

      console.log('🔍 setBounds 输入:', JSON.stringify(validAttractions.map(a => ({
        name: a.name,
        lng: a.lng,
        lat: a.lat,
        lngType: typeof a.lng,
        latType: typeof a.lat,
        lngNum: Number(a.lng),
        latNum: Number(a.lat)
      })), null, 2))
      // ✅ setBounds 也在回调里
      if (validAttractions.length > 0) {
        try {
          const lngs = validAttractions.map(a => Number(a.lng)).filter(v => !isNaN(v) && isFinite(v))
          const lats = validAttractions.map(a => Number(a.lat)).filter(v => !isNaN(v) && isFinite(v))

          if (lngs.length > 0 && lats.length > 0) {
            const centerLng = (Math.min(...lngs) + Math.max(...lngs)) / 2
            const centerLat = (Math.min(...lats) + Math.max(...lats)) / 2

            // 根据跨距计算合适 zoom（简单版：先固定 11）
            map.setZoomAndCenter(11, [centerLng, centerLat])
            console.log(`✅ setZoomAndCenter: ${centerLng}, ${centerLat}`)
          }
        } catch (e) {
          console.warn('❌ setZoomAndCenter 异常:', e)
        }
      }

      mapInstance = map
      console.log('✅ 地图初始化完成:', mapId.value)

      setTimeout(() => {
        if (mapInstance) mapInstance.resize()
      }, 100)
      setTimeout(() => {
        if (mapInstance) mapInstance.resize()
      }, 500)

    } catch (e) {
      console.warn('❌ 地图加载失败:', e)
    }
  })
}

// ✅ 关键3：监听 window resize
const handleResize = () => {
  if (resizeTimer) clearTimeout(resizeTimer)
  resizeTimer = setTimeout(() => {
    if (mapInstance) {
      mapInstance.resize()
      console.log('🔄 resize 事件触发，地图已刷新:', mapId.value)
    } else {
      // 如果地图还没初始化，尝试重新初始化
      initMap()
    }
  }, 100)
}

onMounted(() => {
  // 延迟初始化，等弹窗动画完成
  setTimeout(() => initMap(), 500)
  window.addEventListener('resize', handleResize)
})

watch(() => props.attractions, () => {
  initMap()
}, {deep: true})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  if (retryTimer) clearTimeout(retryTimer)
  if (resizeTimer) clearTimeout(resizeTimer)
  if (mapInstance) {
    mapInstance.destroy()
    mapInstance = null
  }
})
</script>

<style scoped>
.day-map-wrapper {
  margin-top: 14px;
  padding: 12px;
  background: #f7f9fc;
  border-radius: 10px;
  border: 1px solid #e5eaf3;
}

.day-map-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  font-size: 13px;
  color: #4a5568;
}

.day-map-icon {
  font-size: 15px;
}

.day-map-title {
  font-weight: 600;
  flex: 1;
}

.day-map-count {
  font-size: 12px;
  color: #9ca3af;
  background: #eef2ff;
  padding: 2px 8px;
  border-radius: 10px;
}

.day-map-container {
  width: 100%;
  height: 260px;
  border-radius: 8px;
  overflow: hidden;
  background: #e8edf5;
}

.day-map-routes {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.route-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  background: #ffffff;
  border-radius: 6px;
  font-size: 12px;
  color: #4a5568;
}

.route-index {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  color: white;
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-weight: bold;
}

.route-text {
  flex: 1;
}

.route-distance,
.route-duration {
  font-size: 11px;
  color: #6b7280;
  background: #f3f4f6;
  padding: 1px 6px;
  border-radius: 8px;
}

@media (max-width: 640px) {
  .day-map-container {
    height: 180px;
  }

  .route-item {
    font-size: 11px;
  }
}
</style>