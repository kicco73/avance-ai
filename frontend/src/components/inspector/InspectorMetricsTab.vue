<script setup>
import { ref } from 'vue'
import { getMetrics } from '../../api.js'
import { useSignalChangeFlash } from './signalDisplay.js'

const props = defineProps({
  untilMessageId: { type: [Number, String], default: null }
})

const metricsLoading = ref(false)
const metrics = ref([])
const { recentlyChanged: recentlyChangedMetrics, markChanged: markMetricsChanged } = useSignalChangeFlash()

async function loadMetrics() {
  metricsLoading.value = true
  try {
    const nextMetrics = await getMetrics(props.untilMessageId ?? undefined)
    markMetricsChanged(metrics.value, nextMetrics)
    metrics.value = nextMetrics
  } catch {} finally {
    metricsLoading.value = false
  }
}

defineExpose({ loadMetrics })
</script>

<template>
  <div class="inspector-metrics-section">
    <p v-if="metricsLoading" class="signals-status">Loading…</p>
    <p v-else-if="!metrics.length" class="signals-status">No metrics computed yet.</p>
    <div v-else class="inspector-signal-list">
      <div v-for="metric in metrics" :key="metric.name" class="inspector-signal-block">
        <div class="inspector-signal-header">
          <span class="inspector-detail-badge inspector-detail-badge-metric">Metric</span>
          <span class="inspector-signal-name">{{ metric.ui_label || metric.name }}</span>
        </div>
        <span v-if="metric.ui_description" class="inspector-signal-ui_description">{{ metric.ui_description }}</span>
        <div class="inspector-signal-bar-track">
          <div class="inspector-signal-bar-fill" :class="{ 'inspector-signal-bar-changed': recentlyChangedMetrics.has(metric.name) }" :style="{ width: metric.value + '%' }"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.inspector-metrics-section { flex: 1; min-height: 0; overflow-y: auto; }
.signals-status { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-signal-list { display: flex; flex-direction: column; gap: 0.6rem; }
.inspector-signal-block { display: flex; flex-direction: column; gap: 0.2rem; padding: 0.6rem 0.75rem; border-radius: 8px; border: 1px solid #eee; background: #fafafa; }
.inspector-signal-header { display: flex; align-items: center; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-metric { background: #ad1457; }
.inspector-signal-name { font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-signal-ui_description { font-size: 0.78rem; color: #666; line-height: 1.4; }
.inspector-signal-bar-track { position: relative; margin-top: 0.4rem; height: 10px; border-radius: 999px; background: #eee; overflow: visible; }
.inspector-signal-bar-fill { height: 100%; background: #4a6fa5; border-radius: 999px; transition: width 0.3s ease; }
@keyframes inspector-signal-bar-flash { 0% { box-shadow: 0 0 0 0 rgba(74, 111, 165, 0.7); filter: brightness(1.35); } 70% { box-shadow: 0 0 0 5px rgba(74, 111, 165, 0); } 100% { box-shadow: 0 0 0 0 rgba(74, 111, 165, 0); filter: brightness(1); } }
.inspector-signal-bar-changed { animation: inspector-signal-bar-flash 0.9s ease-out; }
</style>
