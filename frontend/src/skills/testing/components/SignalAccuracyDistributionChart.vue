<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Chart, BarController, BarElement, LinearScale, CategoryScale, Tooltip } from 'chart.js'

Chart.register(BarController, BarElement, LinearScale, CategoryScale, Tooltip)

const props = defineProps({
  // Fixed-width bucket counts across the 0..100% scale, low to high —
  // see Statistics.DISTRIBUTION_BUCKET_COUNT in the backend's metrics
  // framework. Bucket count/width is derived from this array's own
  // length, never hardcoded, so either side can change it independently.
  distribution: { type: Array, default: () => [] },
})

const BAR_COLOR = '#4a6fa5'
const BAR_HOVER_COLOR = '#3a5a8a'

const canvasEl = ref(null)
let chart = null

function bucketLabels(count) {
  const width = 100 / count
  return Array.from({ length: count }, (_, i) => `${Math.round(i * width)}-${Math.round((i + 1) * width)}%`)
}

function renderChart() {
  if (!props.distribution.length || !canvasEl.value) {
    if (chart) {
      chart.destroy()
      chart = null
    }
    return
  }
  const labels = bucketLabels(props.distribution.length)
  if (chart) {
    chart.data.labels = labels
    chart.data.datasets[0].data = props.distribution
    chart.update()
    return
  }
  chart = new Chart(canvasEl.value, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: props.distribution,
        backgroundColor: BAR_COLOR,
        hoverBackgroundColor: BAR_HOVER_COLOR,
        borderRadius: 4,
        categoryPercentage: 0.9,
        barPercentage: 0.9,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { top: 10 } },
      scales: {
        x: { grid: { display: false }, ticks: { font: { size: 9.5 }, maxRotation: 0, autoSkip: false } },
        y: { beginAtZero: true, ticks: { precision: 0, font: { size: 10.2 } } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#333',
          titleFont: { size: 11 },
          bodyFont: { size: 11 },
          padding: 8,
          cornerRadius: 6,
          callbacks: {
            title: (items) => items[0].label,
            label: (item) => `${item.parsed.y} observation${item.parsed.y === 1 ? '' : 's'}`,
          },
        },
      },
    },
  })
}

watch(() => props.distribution, async () => {
  await nextTick()
  renderChart()
}, { deep: true })

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
  <div class="signal-accuracy-distribution-chart">
    <p v-if="!distribution.length" class="signal-accuracy-distribution-chart-status">No samples yet.</p>
    <canvas v-else ref="canvasEl"></canvas>
  </div>
</template>

<style scoped>
.signal-accuracy-distribution-chart {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.signal-accuracy-distribution-chart-status {
  margin: 0;
  padding: 0.75rem 0;
  font-size: 0.9rem;
  color: #666;
}
</style>
