<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Chart, LineController, LineElement, PointElement, LinearScale, TimeScale, Tooltip } from 'chart.js'
import zoomPlugin from 'chartjs-plugin-zoom'
import 'chartjs-adapter-date-fns'
import { getMetrics, getMetricsHistory } from '../../api.js'

Chart.register(LineController, LineElement, PointElement, LinearScale, TimeScale, Tooltip, zoomPlugin)

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
const SESSION_START_COLOR = '#9e9e9e'
const LINE_HIT_TOLERANCE_PX = 5
const POINT_HIT_TOLERANCE_PX = 20
// A line whose timestamp is the domain's own min/max lands exactly on
// the axis border and blends into it — nudged inward so it stays
// visually distinct from the axis rather than looking absent.
const LINE_EDGE_INSET_PX = 2
const TOOLTIP_MAX_WIDTH = 220
const TOOLTIP_MARGIN = 12

const sessionStartPlugin = {
  id: 'sessionStartLines',
  afterDraw(chart) {
    const { ctx, chartArea, scales } = chart
    ctx.save()
    ctx.strokeStyle = SESSION_START_COLOR
    ctx.setLineDash([4, 3])
    ctx.lineWidth = 1
    sessionStarts.value.forEach((sessionStart) => {
      const rawX = scales.x.getPixelForValue(new Date(sessionStart.timestamp).getTime())
      if (rawX < chartArea.left - LINE_HIT_TOLERANCE_PX || rawX > chartArea.right + LINE_HIT_TOLERANCE_PX) return
      const x = Math.min(Math.max(rawX, chartArea.left + LINE_EDGE_INSET_PX), chartArea.right - LINE_EDGE_INSET_PX)
      ctx.beginPath()
      ctx.moveTo(x, chartArea.top)
      ctx.lineTo(x, chartArea.bottom)
      ctx.stroke()
    })
    ctx.restore()
  },
}

const loading = ref(true)
const history = ref([])
const sessionStarts = ref([])
const metricLabels = ref({})
const canvasEl = ref(null)
const lineTooltip = ref(null)
const lineTooltipStyle = ref({})
const isZoomed = ref(false)
let chart = null

function refreshZoomedState() {
  isZoomed.value = chart?.isZoomedOrPanned() ?? false
}

function resetZoom() {
  chart?.resetZoom()
  isZoomed.value = false
}

function metricLabel(name) {
  return metricLabels.value[name] ?? name
}

function formatTimestamp(timestamp) {
  return new Date(timestamp).toLocaleString([], { hour12: false })
}

function formatValue(value) {
  return `${value.toFixed(1)}%`
}

function tooltipPositionStyle(event) {
  const overflowsRight = event.clientX + TOOLTIP_MARGIN + TOOLTIP_MAX_WIDTH > window.innerWidth
  return overflowsRight
    ? { right: `${window.innerWidth - event.clientX + TOOLTIP_MARGIN}px`, top: `${event.clientY + TOOLTIP_MARGIN}px` }
    : { left: `${event.clientX + TOOLTIP_MARGIN}px`, top: `${event.clientY + TOOLTIP_MARGIN}px` }
}

// Chart.js's own tooltip can't put a color swatch on its title line (only
// body/label rows support labelColor) — replaced outright by this same
// floating tooltip the dashed lines already use, so every hover state
// (line or point) shares one look.
function findNearestPoint(event) {
  if (!chart) return null
  const [match] = chart.getElementsAtEventForMode(event, 'nearest', { intersect: false }, false)
  if (!match) return null
  const rect = canvasEl.value.getBoundingClientRect()
  const el = chart.getDatasetMeta(match.datasetIndex).data[match.index]
  const dx = el.x - (event.clientX - rect.left)
  const dy = el.y - (event.clientY - rect.top)
  if (Math.sqrt(dx * dx + dy * dy) > POINT_HIT_TOLERANCE_PX) return null
  const dataset = chart.data.datasets[match.datasetIndex]
  return { color: dataset.borderColor, name: dataset.label, point: dataset.data[match.index] }
}

function onCanvasMouseMove(event) {
  if (!chart) return
  const { chartArea, scales } = chart
  const rect = canvasEl.value.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  const lineMatch = y >= chartArea.top && y <= chartArea.bottom
    ? sessionStarts.value.find(
      (entry) => Math.abs(scales.x.getPixelForValue(new Date(entry.timestamp).getTime()) - x) <= LINE_HIT_TOLERANCE_PX,
    )
    : null
  if (lineMatch) {
    lineTooltip.value = { color: null, title: lineMatch.title, subtitle: `Ended: ${formatTimestamp(lineMatch.end_timestamp)}` }
    lineTooltipStyle.value = tooltipPositionStyle(event)
    return
  }
  const pointMatch = findNearestPoint(event)
  if (pointMatch) {
    lineTooltip.value = {
      color: pointMatch.color,
      title: `${pointMatch.name}: ${formatValue(pointMatch.point.y)}`,
      subtitle: formatTimestamp(pointMatch.point.x),
    }
    lineTooltipStyle.value = tooltipPositionStyle(event)
    return
  }
  lineTooltip.value = null
}

function onCanvasMouseLeave() {
  lineTooltip.value = null
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
      // 'monotone', not a `tension` value: a plain cubic bezier can
      // overshoot past a sharp, isolated spike and loop back on itself
      // (visibly crossing the line) — monotone interpolation stays
      // smooth without ever overshooting a point's own value.
      cubicInterpolationMode: 'monotone',
      pointRadius: 2.5,
      fill: false,
    }
  })
}

// The axis always starts at 0, but its ceiling tracks the data instead
// of always reserving headroom up to 100 — 10% slack above the highest
// point, capped at 100.
function computeYMax(datasets) {
  const values = datasets.flatMap((dataset) => dataset.data.map((point) => point.y))
  return values.length ? Math.min(Math.ceil(Math.max(...values) * 1.1), 100) : 100
}

function renderChart() {
  if (history.value.length < 2) {
    if (chart) {
      chart.destroy()
      chart = null
    }
    return
  }
  const datasets = buildDatasets(history.value)
  const yMax = computeYMax(datasets)
  if (chart) {
    // A fresh load (new user/project, or the same one's data changed) —
    // an old zoom window pinned to different timestamps would otherwise
    // carry over onto data it no longer matches.
    chart.resetZoom('none')
    isZoomed.value = false
    chart.data.datasets = datasets
    chart.options.scales.y.max = yMax
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
        y: { min: 0, max: yMax, ticks: { stepSize: 50, callback: (value) => `${value}%`, font: { size: 10.2 } } },
      },
      interaction: { mode: 'nearest', intersect: false },
      plugins: {
        legend: { display: false },
        // Replaced by our own floating tooltip (onCanvasMouseMove) — the
        // color swatch it needs on the title line isn't something
        // Chart.js's own tooltip callbacks can do.
        tooltip: { enabled: false },
        // x-only: panning/zooming the y-axis would misrepresent the
        // fixed 0-100% scale every value on this chart is measured against.
        zoom: {
          pan: { enabled: true, mode: 'x', onPanComplete: refreshZoomedState },
          zoom: {
            wheel: { enabled: true },
            pinch: { enabled: true },
            mode: 'x',
            onZoomComplete: refreshZoomedState,
          },
        },
      },
    },
    plugins: [sessionStartPlugin],
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
    sessionStarts.value = []
    return
  }
  loading.value = true
  try {
    await loadMetricLabels()
    const data = await getMetricsHistory(props.projectName, props.username)
    history.value = data.metrics
    sessionStarts.value = data.session_starts
  } catch {
    history.value = []
    sessionStarts.value = []
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
    <div v-else-if="history.length >= 2" class="metrics-trends-canvas-wrap">
      <button v-if="isZoomed" class="trend-reset-zoom-btn" @click="resetZoom">Reset zoom</button>
      <canvas ref="canvasEl" @mousemove="onCanvasMouseMove" @mouseleave="onCanvasMouseLeave"></canvas>
    </div>
  </div>
  <Teleport to="body">
    <div v-if="lineTooltip" class="trend-line-tooltip-floating" :style="lineTooltipStyle">
      <div class="trend-line-tooltip-title">
        <span v-if="lineTooltip.color" class="trend-line-tooltip-swatch" :style="{ background: lineTooltip.color }"></span>
        {{ lineTooltip.title }}
      </div>
      <div class="trend-line-tooltip-subtitle">{{ lineTooltip.subtitle }}</div>
    </div>
  </Teleport>
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

.trend-reset-zoom-btn {
  position: absolute;
  top: 0.25rem;
  right: 0.25rem;
  z-index: 10;
  padding: 0.25rem 0.6rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  font-size: 0.72rem;
  cursor: pointer;
}

.trend-reset-zoom-btn:hover {
  background: #4a6fa5;
  color: white;
}

.metrics-trends-canvas-wrap {
  flex: 1;
  min-height: 0;
  position: relative;
}

/* Teleported to <body>, positioned in viewport coordinates — fixed, not
   absolute, since a narrow settings panel would otherwise clip it. */
.trend-line-tooltip-floating {
  position: fixed;
  width: max-content;
  max-width: 220px;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  background: #333;
  color: white;
  font-family: system-ui, -apple-system, sans-serif;
  font-size: 0.72rem;
  line-height: 1.3;
  text-align: left;
  pointer-events: none;
  z-index: 1000;
}

.trend-line-tooltip-title {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-weight: 600;
}

.trend-line-tooltip-swatch {
  flex-shrink: 0;
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 2px;
}

.trend-line-tooltip-subtitle {
  opacity: 0.8;
  margin-top: 0.15rem;
}
</style>
