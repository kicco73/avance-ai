<script setup>
import { ref } from 'vue'
import { getMetrics } from '../../api.js'
import DocInfoButton from '../DocInfoButton.vue'
import MetricDetail from './MetricDetail.vue'
import { useSignalChangeFlash } from './signalDisplay.js'

const props = defineProps({
  untilMessageId: { type: [Number, String], default: null },
  projectId: { type: String, required: true }
})

const metricsLoading = ref(false)
const metrics = ref([])
const { recentlyChanged: recentlyChangedMetrics, markChanged: markMetricsChanged } = useSignalChangeFlash()

async function loadMetrics() {
  metricsLoading.value = true
  try {
    const nextMetrics = await getMetrics(props.projectId, props.untilMessageId ?? undefined)
    markMetricsChanged(metrics.value, nextMetrics)
    metrics.value = nextMetrics
  } catch {} finally {
    metricsLoading.value = false
  }
}

// Heavier to compute than Signals/Env, so this only reloads while the tab
// is actually visible.
async function refresh(active) {
  if (active) await loadMetrics()
}

defineExpose({ loadMetrics, refresh })
</script>

<template>
  <div class="inspector-metrics-section">
    <div class="inspector-metrics-header">
      <DocInfoButton doc-name="metrics" title="Core metrics" />
    </div>
    <p v-if="metricsLoading" class="signals-status">Loading…</p>
    <p v-else-if="!metrics.length" class="signals-status">No metrics computed yet.</p>
    <div v-else class="inspector-signal-list">
      <MetricDetail
        v-for="metric in metrics"
        :key="metric.name"
        :label="metric.ui_label || metric.name"
        :value="metric.value"
        :description="metric.ui_description"
        :highlighted="recentlyChangedMetrics.has(metric.name)"
      />
    </div>
  </div>
</template>

<style scoped>
.inspector-metrics-section { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
.inspector-metrics-header { display: flex; justify-content: flex-end; margin-bottom: 0.5rem; flex-shrink: 0; }
.signals-status { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-signal-list { display: flex; flex-direction: column; gap: 0.6rem; }
</style>
