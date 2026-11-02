<template>
  <div class="day-map-wrapper">
    <div class="day-map-header">
      <span class="day-map-icon">🗺️</span>
      <span class="day-map-title">第 {{ day }} 天路线地图</span>
      <span
          v-if="approxCount"
          class="day-map-approx"
          title="这些景点没能精确定位到具体地点，坐标是按城市中心估算的"
      >{{ approxCount }} 个估算位置</span>
      <span class="day-map-count">{{ attractions.length }} 个景点</span>
    </div>
    <div v-if="isEmpty" class="day-map-empty">
      <span class="day-map-empty-icon">🧭</span>
      <span class="day-map-empty-text">这一天没有需要标注的景点，可能是自由活动或返程。</span>
    </div>
    <div
        v-else
        :id="mapId"
        class="day-map-container"
    >
      <div v-if="mapError" class="day-map-error">
        <span class="day-map-error-icon">⚠️</span>
        <span class="day-map-error-text">{{ mapError }}</span>
      </div>
    </div>
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
import {loadAMap} from '@/utils/amap'

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
const mapError = ref('')
let mapInstance = null
let retryTimer = null
let resizeTimer = null
let tilesTimer = null

const colors = ['#4f6ef7', '#ef4444', '#22c55e', '#f59e0b', '#8b5cf6']
const getColor = (idx) => colors[idx % colors.length]

const approxCount = computed(() => (props.attractions || []).filter(a => a && a.approx).length)

// 某天一个景点都没提取到（自由活动/返程），就别画一张空白地图，
// 给一句说明——否则用户只会以为地图坏了。
const isEmpty = computed(() => !(props.attractions || []).length)

// 同一坐标的多个景点会互相遮挡，看起来像"少了一个点"
// （后端以前把查不到坐标的景点全画在城市中心点上，就出现过这种情况）。
// 这里对重复坐标做小半径错开，保证每个点都能看见；顺带标记为估算位置。
const spreadDuplicates = (list) => {
  const seen = new Map()
  return list.map(a => {
    const key = `${Number(a.lng).toFixed(5)},${Number(a.lat).toFixed(5)}`
    const n = seen.get(key) || 0
    seen.set(key, n + 1)
    if (n === 0) return a
    const angle = (Math.PI * 2 * n) / 6
    return {
      ...a,
      lng: Number(a.lng) + 0.0006 * Math.cos(angle),
      lat: Number(a.lat) + 0.0006 * Math.sin(angle),
      approx: true
    }
  })
}

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

  nextTick(async () => {
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

    // ✅ 等脚本就绪；失败时把原因显示在容器里，而不是留一个灰块
    let AMap
    try {
      AMap = await loadAMap()
      mapError.value = ''
    } catch (e) {
      console.error('❌ 高德地图加载失败:', e.message)
      mapError.value = e.message
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
      const validAttractions = spreadDuplicates(props.attractions.filter(a => {
        const valid = a &&
            Number.isFinite(Number(a.lng)) && Number.isFinite(Number(a.lat))
        if (!valid) console.warn(`❌ 无效坐标: ${a?.name} lng=${a?.lng} lat=${a?.lat}`)
        return valid
      }))

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
        mapError.value = '这一天的景点缺少经纬度，暂时无法绘制地图'
        return
      }

      // ✅ 创建地图
      const map = new AMap.Map(mapId.value, {
        zoom: 12,
        center: [centerLng, centerLat],
        mapStyle: 'amap://styles/whitesmoke',
        showIndoorMap: false,
        resizeEnable: true
      })

      // 判断底图是否真的渲染出来：Key 类型不对 / 缺安全密钥 / 域名白名单不匹配时，
      // AMap 对象是存在的，new Map() 也不报错，只是瓦片请求被拒 —— 表现就是一个灰块。
      let tilesReady = false
      map.on('complete', () => {
        tilesReady = true
        mapError.value = ''
      })
      if (tilesTimer) clearTimeout(tilesTimer)
      tilesTimer = setTimeout(() => {
        if (!tilesReady) {
          console.warn('⚠️ 底图 8 秒内未渲染完成')
          mapError.value = '底图未加载：Key 可能不是「Web端(JS API)」类型，或缺安全密钥、域名白名单不含当前地址'
        }
      }, 8000)

      // ✅ marker 循环必须在 nextTick 回调里，这样能访问到 map
      validAttractions.forEach((attr, index) => {
        try {
          // approx=true：后端没能精确定位，坐标是城市中心附近的估算值，用灰色 + ≈ 标出来
          const isApprox = !!attr.approx
          const badgeBg = isApprox ? '#94a3b8' : '#4f6ef7'
          const marker = new AMap.Marker({
            position: [Number(attr.lng), Number(attr.lat)],
            title: isApprox ? `${attr.name}（位置为估算）` : attr.name,
            label: {
              content: `<div style="font-size:11px;padding:3px 8px;background:${badgeBg};color:white;border-radius:10px;font-weight:bold;white-space:nowrap;${isApprox ? 'border:1px dashed #fff;' : ''}">${index + 1}. ${attr.name}${isApprox ? ' ≈' : ''}</div>`,
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
              const polyline = new AMap.Polyline({
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
                const polyline = new AMap.Polyline({
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
  if (tilesTimer) clearTimeout(tilesTimer)
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

.day-map-approx {
  font-size: 11px;
  color: #b45309;
  background: #fef3c7;
  padding: 2px 8px;
  border-radius: 10px;
  cursor: help;
}

.day-map-container {
  position: relative;
  width: 100%;
  height: 260px;
  border-radius: 8px;
  overflow: hidden;
  background: #e8edf5;
}

.day-map-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 120px;
  padding: 16px;
  border-radius: 8px;
  border: 1px dashed #cdd6e4;
  background: #f7f9fc;
  color: #6b7280;
  font-size: 12px;
  text-align: center;
}

.day-map-empty-icon {
  font-size: 20px;
  line-height: 1;
}

.day-map-error {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 16px;
  text-align: center;
  background: #fff6f6;
  color: #b42318;
  font-size: 12px;
  line-height: 1.6;
}

.day-map-error-icon {
  font-size: 20px;
}

.day-map-error-text {
  max-width: 90%;
  word-break: break-all;
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