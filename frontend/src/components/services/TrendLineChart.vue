<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Chart, LineController, LineElement, PointElement, LinearScale, TimeScale, Tooltip } from 'chart.js'
import zoomPlugin from 'chartjs-plugin-zoom'
import 'chartjs-adapter-date-fns'

Chart.register(LineController, LineElement, PointElement, LinearScale, TimeScale, Tooltip, zoomPlugin)

const props = defineProps({
  history: { type: Array, default: () => [] },
  markers: { type: Array, default: () => [] },
  providerLabels: { type: Object, default: () => ({}) }
})

const PALETTE = [
  '#4a6fa5', '#c9974a', '#5c8f72', '#4b52ad', '#c7c056', '#589c8c', '#4589a0', '#c26948',
  '#519157', '#684db5', '#abc95d', '#5b9ba2', '#419c95', '#c04149', '#5c8b4e', '#8f51b9',
]
const POINT_HIT_TOLERANCE_PX = 20
const TOOLTIP_MAX_WIDTH = 220
const TOOLTIP_MARGIN = 12
const MARKER_DASH = [4, 4]
const MARKER_LABEL_OFFSET_PX = 4
const MARKER_LABEL_FONT = '10px system-ui, -apple-system, sans-serif'

const markersPlugin = {
  id: 'markers',
  afterDatasetsDraw(chart, _args, options) {
    const { ctx, chartArea, scales } = chart
    for (const marker of options.markers) {
      const x = scales.x.getPixelForValue(new Date(marker.timestamp).getTime())
      if (x < chartArea.left || x > chartArea.right) continue
      ctx.save()
      ctx.strokeStyle = marker.color
      ctx.fillStyle = marker.color
      ctx.lineWidth = 1
      ctx.setLineDash(MARKER_DASH)
      ctx.beginPath()
      ctx.moveTo(x, chartArea.top)
      ctx.lineTo(x, chartArea.bottom)
      ctx.stroke()
      ctx.setLineDash([])
      ctx.font = MARKER_LABEL_FONT
      ctx.textBaseline = 'top'
      const fitsRight = x + MARKER_LABEL_OFFSET_PX + ctx.measureText(marker.label).width <= chartArea.right
      ctx.textAlign = fitsRight ? 'left' : 'right'
      ctx.fillText(marker.label, fitsRight ? x + MARKER_LABEL_OFFSET_PX : x - MARKER_LABEL_OFFSET_PX, chartArea.top + 2)
      ctx.restore()
    }
  },
}

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

function providerLabel(key) {
  return props.providerLabels[key] ?? key
}

function formatTimestamp(timestamp) {
  return new Date(timestamp).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function tooltipPositionStyle(event) {
  const overflowsRight = event.clientX + TOOLTIP_MARGIN + TOOLTIP_MAX_WIDTH > window.innerWidth
  return overflowsRight
    ? { right: `${window.innerWidth - event.clientX + TOOLTIP_MARGIN}px`, top: `${event.clientY + TOOLTIP_MARGIN}px` }
    : { left: `${event.clientX + TOOLTIP_MARGIN}px`, top: `${event.clientY + TOOLTIP_MARGIN}px` }
}

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
  const pointMatch = findNearestPoint(event)
  if (!pointMatch) {
    lineTooltip.value = null
    return
  }
  lineTooltip.value = {
    color: pointMatch.color,
    name: pointMatch.name,
    value: pointMatch.point.y,
    subtitle: formatTimestamp(pointMatch.point.x),
  }
  lineTooltipStyle.value = tooltipPositionStyle(event)
}

function onCanvasMouseLeave() {
  lineTooltip.value = null
}

function seriesKeys(entries) {
  return [...new Set(entries.flatMap((entry) => Object.keys(entry.values)))].sort()
}

function seriesColor(key, entries) {
  const index = seriesKeys(entries).indexOf(key)
  return PALETTE[(index < 0 ? 0 : index) % PALETTE.length]
}

function buildMarkers(entries) {
  return props.markers.map((marker) => ({
    timestamp: marker.timestamp,
    label: marker.label ?? providerLabel(marker.key),
    color: seriesColor(marker.key, entries),
  }))
}

function buildDatasets(entries) {
  return seriesKeys(entries).map((key, index) => ({
    label: providerLabel(key),
    data: entries.map((entry) => ({ x: entry.timestamp, y: entry.values[key] })).filter((point) => point.y != null),
    borderColor: PALETTE[index % PALETTE.length],
    backgroundColor: PALETTE[index % PALETTE.length],
    cubicInterpolationMode: 'monotone',
    pointRadius: 2.5,
    fill: false,
  }))
}

function computeYMax(datasets) {
  const values = datasets.flatMap((dataset) => dataset.data.map((point) => point.y))
  return values.length ? Math.ceil(Math.max(...values) * 1.1) : 10
}

function renderChart() {
  if (props.history.length < 2) {
    if (chart) {
      chart.destroy()
      chart = null
    }
    return
  }
  const datasets = buildDatasets(props.history)
  const markers = buildMarkers(props.history)
  const yMax = computeYMax(datasets)
  if (chart) {
    chart.resetZoom('none')
    isZoomed.value = false
    chart.data.datasets = datasets
    chart.options.plugins.markers.markers = markers
    chart.options.scales.y.max = yMax
    chart.update()
    return
  }
  if (!canvasEl.value) return
  chart = new Chart(canvasEl.value, {
    type: 'line',
    data: { datasets },
    plugins: [markersPlugin],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { type: 'time', time: { unit: 'minute' } },
        y: { min: 0, max: yMax, ticks: { callback: (value) => value.toLocaleString(), font: { size: 10.2 } } },
      },
      interaction: { mode: 'nearest', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false },
        markers: { markers },
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
  })
}

watch(() => [props.history, props.markers], async () => {
  await nextTick()
  renderChart()
})

onMounted(async () => {
  await nextTick()
  renderChart()
})

onBeforeUnmount(() => {
  if (chart) {
    chart.destroy()
    chart = null
  }
})
</script>

<template>
  <div class="trend-line-chart">
    <div class="trend-line-chart-canvas-wrap">
      <button v-if="isZoomed" class="trend-reset-zoom-btn" @click="resetZoom">Reset zoom</button>
      <canvas ref="canvasEl" @mousemove="onCanvasMouseMove" @mouseleave="onCanvasMouseLeave"></canvas>
    </div>
  </div>
  <Teleport to="body">
    <div v-if="lineTooltip" class="trend-line-tooltip-floating" :style="lineTooltipStyle">
      <div class="trend-line-tooltip-title">
        <span v-if="lineTooltip.color" class="trend-line-tooltip-swatch" :style="{ background: lineTooltip.color }"></span>
        {{ lineTooltip.name }}: <slot name="value" :value="lineTooltip.value">{{ lineTooltip.value }}</slot>
      </div>
      <div class="trend-line-tooltip-subtitle">{{ lineTooltip.subtitle }}</div>
    </div>
  </Teleport>
</template>

<style scoped>
.trend-line-chart {
  height: 100%;
  display: flex;
  flex-direction: column;
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

.trend-line-chart-canvas-wrap {
  flex: 1;
  min-height: 0;
  position: relative;
}

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
