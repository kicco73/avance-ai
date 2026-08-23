<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Chart, LineController, LineElement, PointElement, LinearScale, TimeScale, Tooltip } from 'chart.js'
import 'chartjs-adapter-date-fns'
import { getMetrics, getMetricsHistory } from '../../api.js'

Chart.register(LineController, LineElement, PointElement, LinearScale, TimeScale, Tooltip)

const props = defineProps({
  projectName: { type: String, required: true },
  username: { type: String, required: true },
})

const emit = defineEmits(['colors'])

const PALETTE = [
  '#4a6fa5', '#c9974a', '#5c8f72', '#4b52ad', '#c7c056', '#589c8c', '#4589a0', '#c26948',
  '#519157', '#684db5', '#abc95d', '#5b9ba2', '#419c95', '#c04149', '#5c8b4e', '#8f51b9',
  '#89c95e', '#6188a6', '#3d9772', '#ba3e6f', '#6c854b', '#b656bd', '#65c95e', '#6776a9',
  '#399250', '#b33c92', '#797f47', '#c25ba7', '#5ec97c', '#726bae', '#3b8d35', '#a539ac',
]
const SMOOTHING_WINDOW = 3

const loading = ref(true)
const history = ref([])
const metricLabels = ref({})
const canvasEl = ref(null)
let chart = null

function metricLabel(name) {
  return metricLabels.value[name] ?? name
}

function movingAverage(values) {
  return values.map((_, i) => {
    const start = Math.max(0, i - Math.floor(SMOOTHING_WINDOW / 2))
    const end = Math.min(values.length, i + Math.ceil(SMOOTHING_WINDOW / 2))
    const slice = values.slice(start, end)
    return slice.reduce((sum, v) => sum + v, 0) / slice.length
  })
}

function metricKeys(entries) {
  return [...new Set(entries.flatMap((entry) => Object.keys(entry.values)))].sort()
}

function buildDatasets(entries) {
  const keys = metricKeys(entries)
  return keys.map((key, index) => {
    const points = entries
      .map((entry) => ({ x: entry.timestamp, y: entry.values[key] }))
      .filter((point) => point.y != null)
    const smoothedY = movingAverage(points.map((point) => point.y))
    return {
      label: metricLabel(key),
      data: points.map((point, i) => ({ x: point.x, y: smoothedY[i] })),
      borderColor: PALETTE[index % PALETTE.length],
      backgroundColor: PALETTE[index % PALETTE.length],
      tension: 0.35,
      pointRadius: 2.5,
      fill: false,
    }
  })
}

function renderChart() {
  if (!history.value.length) {
    if (chart) {
      chart.destroy()
      chart = null
    }
    return
  }
  const datasets = buildDatasets(history.value)
  if (chart) {
    chart.data.datasets = datasets
    chart.update()
    return
  }
  if (!canvasEl.value) return
  chart = new Chart(canvasEl.value, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { type: 'time', time: { unit: 'day' } },
        y: { min: 0, max: 100, ticks: { stepSize: 50 } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { mode: 'nearest', intersect: false },
      },
    },
  })
}

async function loadMetricLabels() {
  try {
    const metrics = await getMetrics(props.projectName, null, true, props.username)
    metricLabels.value = Object.fromEntries(metrics.map((metric) => [metric.name, metric.ui_label || metric.name]))
  } catch {
    metricLabels.value = {}
  }
}

async function load() {
  if (!props.projectName || !props.username) {
    history.value = []
    return
  }
  loading.value = true
  try {
    await loadMetricLabels()
    history.value = await getMetricsHistory(props.projectName, props.username)
  } catch {
    history.value = []
  } finally {
    loading.value = false
  }
  emit('colors', Object.fromEntries(metricKeys(history.value).map((key, i) => [key, PALETTE[i % PALETTE.length]])))
  await nextTick()
  renderChart()
}

watch(() => [props.projectName, props.username], load)

onMounted(load)

onBeforeUnmount(() => {
  if (chart) {
    chart.destroy()
    chart = null
  }
})
</script>

<template>
  <div class="metrics-trends">
    <p v-if="loading" class="metrics-trends-status">Loading…</p>
    <p v-else-if="!history.length" class="metrics-trends-status">No metrics recorded yet for this user in this project.</p>
    <div v-else class="metrics-trends-canvas-wrap">
      <canvas ref="canvasEl"></canvas>
    </div>
  </div>
</template>

<style scoped>
.metrics-trends {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.metrics-trends-status {
  margin: 0;
  padding: 0.75rem 0;
  font-size: 0.9rem;
  color: #666;
}

.metrics-trends-canvas-wrap {
  flex: 1;
  min-height: 0;
  position: relative;
}
</style>
