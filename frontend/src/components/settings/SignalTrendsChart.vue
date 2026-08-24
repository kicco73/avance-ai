<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Chart, LineController, LineElement, PointElement, LinearScale, TimeScale, Tooltip } from 'chart.js'
import 'chartjs-adapter-date-fns'
import { getProjectGraph, getProjectSignals, getTimeline } from '../../api.js'

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
const STATE_CHANGE_COLOR = '#9e9e9e'
const LINE_HIT_TOLERANCE_PX = 5
// A line whose timestamp is the domain's own min/max lands exactly on
// the axis border and blends into it — nudged inward so it stays
// visually distinct from the axis rather than looking absent.
const LINE_EDGE_INSET_PX = 2
const TOOLTIP_MAX_WIDTH = 220
const TOOLTIP_MARGIN = 12

const stateChangePlugin = {
  id: 'stateChangeLines',
  afterDraw(chart) {
    const { ctx, chartArea, scales } = chart
    ctx.save()
    ctx.strokeStyle = STATE_CHANGE_COLOR
    ctx.fillStyle = STATE_CHANGE_COLOR
    ctx.setLineDash([4, 3])
    ctx.lineWidth = 1
    ctx.font = '10px system-ui, -apple-system, sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'bottom'
    transitions.value.forEach((transition) => {
      const rawX = scales.x.getPixelForValue(new Date(transition.timestamp).getTime())
      if (rawX < chartArea.left - LINE_HIT_TOLERANCE_PX || rawX > chartArea.right + LINE_HIT_TOLERANCE_PX) return
      const x = Math.min(Math.max(rawX, chartArea.left + LINE_EDGE_INSET_PX), chartArea.right - LINE_EDGE_INSET_PX)
      ctx.beginPath()
      ctx.moveTo(x, chartArea.top)
      ctx.lineTo(x, chartArea.bottom)
      ctx.stroke()
      ctx.fillText(stateLabel(transition.new_state), x, chartArea.top - 2)
    })
    ctx.restore()
  },
}

const loading = ref(true)
const history = ref([])
const transitions = ref([])
const signalLabels = ref({})
const stateLabels = ref({})
const canvasEl = ref(null)
const lineTooltip = ref(null)
const lineTooltipStyle = ref({})
let chart = null

function signalLabel(name) {
  return signalLabels.value[name] ?? name
}

function stateLabel(key) {
  return stateLabels.value[key] ?? key
}

function formatTimestamp(timestamp) {
  return new Date(timestamp).toLocaleString()
}

function tooltipPositionStyle(event) {
  const overflowsRight = event.clientX + TOOLTIP_MARGIN + TOOLTIP_MAX_WIDTH > window.innerWidth
  return overflowsRight
    ? { right: `${window.innerWidth - event.clientX + TOOLTIP_MARGIN}px`, top: `${event.clientY + TOOLTIP_MARGIN}px` }
    : { left: `${event.clientX + TOOLTIP_MARGIN}px`, top: `${event.clientY + TOOLTIP_MARGIN}px` }
}

// Chart.js's own point tooltip fights our line tooltip for the same
// screen space when both would be visible at once — suppressed while
// hovering a line, restored as soon as the cursor leaves it.
function setPointTooltipEnabled(enabled) {
  if (!chart || chart.options.plugins.tooltip.enabled === enabled) return
  chart.options.plugins.tooltip.enabled = enabled
  chart.update('none')
}

function onCanvasMouseMove(event) {
  if (!chart) return
  const { chartArea, scales } = chart
  const rect = canvasEl.value.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  const match = y >= chartArea.top && y <= chartArea.bottom
    ? transitions.value.find(
      (entry) => Math.abs(scales.x.getPixelForValue(new Date(entry.timestamp).getTime()) - x) <= LINE_HIT_TOLERANCE_PX,
    )
    : null
  setPointTooltipEnabled(!match)
  if (!match) {
    lineTooltip.value = null
    return
  }
  lineTooltip.value = { title: stateLabel(match.new_state), subtitle: formatTimestamp(match.timestamp) }
  lineTooltipStyle.value = tooltipPositionStyle(event)
}

function onCanvasMouseLeave() {
  lineTooltip.value = null
  setPointTooltipEnabled(true)
}

function movingAverage(values) {
  return values.map((_, i) => {
    const start = Math.max(0, i - Math.floor(SMOOTHING_WINDOW / 2))
    const end = Math.min(values.length, i + Math.ceil(SMOOTHING_WINDOW / 2))
    const slice = values.slice(start, end)
    return slice.reduce((sum, v) => sum + v, 0) / slice.length
  })
}

function signalKeys(entries) {
  return [...new Set(entries.flatMap((entry) => Object.keys(entry.values)))].sort()
}

function buildDatasets(entries) {
  const keys = signalKeys(entries)
  return keys.map((key, index) => {
    const points = entries
      .map((entry) => ({ x: entry.timestamp, y: entry.values[key] }))
      .filter((point) => point.y != null)
    const smoothedY = movingAverage(points.map((point) => point.y))
    return {
      label: signalLabel(key),
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
      layout: { padding: { top: 14 } },
      scales: {
        x: { type: 'time', time: { unit: 'day' } },
        y: { min: 0, max: 100, ticks: { stepSize: 50 } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { mode: 'nearest', intersect: false },
      },
    },
    plugins: [stateChangePlugin],
  })
}

async function loadSignalLabels() {
  try {
    const { signals } = await getProjectSignals(props.projectName)
    signalLabels.value = Object.fromEntries(signals.map((entry) => [entry.signal.name, entry.signal.ui_label || entry.signal.name]))
  } catch {
    signalLabels.value = {}
  }
}

async function loadStateLabels() {
  try {
    const { nodes } = await getProjectGraph(props.projectName)
    stateLabels.value = Object.fromEntries(nodes.map((node) => [node.state.key, node.state.ui_label || node.state.key]))
  } catch {
    stateLabels.value = {}
  }
}

async function load() {
  if (!props.projectName || !props.username) {
    history.value = []
    transitions.value = []
    return
  }
  loading.value = true
  try {
    await Promise.all([loadSignalLabels(), loadStateLabels()])
    const timeline = await getTimeline(props.projectName, props.username)
    history.value = timeline.signals
    transitions.value = timeline.transitions
  } catch {
    history.value = []
    transitions.value = []
  } finally {
    loading.value = false
  }
  emit('colors', Object.fromEntries(signalKeys(history.value).map((key, i) => [key, PALETTE[i % PALETTE.length]])))
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
  <div class="signal-trends">
    <p v-if="loading" class="signal-trends-status">Loading…</p>
    <p v-else-if="!history.length" class="signal-trends-status">No timeline recorded yet for this user in this project.</p>
    <div v-else class="signal-trends-canvas-wrap">
      <canvas ref="canvasEl" @mousemove="onCanvasMouseMove" @mouseleave="onCanvasMouseLeave"></canvas>
    </div>
  </div>
  <Teleport to="body">
    <div v-if="lineTooltip" class="trend-line-tooltip-floating" :style="lineTooltipStyle">
      <div class="trend-line-tooltip-title">{{ lineTooltip.title }}</div>
      <div class="trend-line-tooltip-subtitle">{{ lineTooltip.subtitle }}</div>
    </div>
  </Teleport>
</template>

<style scoped>
.signal-trends {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.signal-trends-status {
  margin: 0;
  padding: 0.75rem 0;
  font-size: 0.9rem;
  color: #666;
}

.signal-trends-canvas-wrap {
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
  font-size: 0.72rem;
  line-height: 1.3;
  text-align: left;
  pointer-events: none;
  z-index: 1000;
}

.trend-line-tooltip-title {
  font-weight: 600;
}

.trend-line-tooltip-subtitle {
  opacity: 0.8;
  margin-top: 0.15rem;
}
</style>
