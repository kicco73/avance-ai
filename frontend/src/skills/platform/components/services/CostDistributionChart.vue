<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { BarController, BarElement, CategoryScale, Chart, LinearScale, Tooltip } from 'chart.js'

Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip)

const props = defineProps({
  bins: { type: Array, required: true },
  currency: { type: String, required: true }
})

const money = computed(() => new Intl.NumberFormat(undefined, {
  style: 'currency', currency: props.currency, maximumFractionDigits: 4
}))

const BAR_COLOR = '#4a6fa5'
const AXIS_COLOR = '#999'
const GRID_COLOR = '#eee'
const BAR_RADIUS_PX = 4
const BAR_GAP_PX = 2

const canvas = ref(null)
let chart = null

function range(bin) {
  return `${money.value.format(bin.from)} – ${money.value.format(bin.to)}`
}

function draw() {
  chart?.destroy()
  chart = new Chart(canvas.value, {
    type: 'bar',
    data: {
      labels: props.bins.map(range),
      datasets: [{
        data: props.bins.map((bin) => bin.count),
        backgroundColor: BAR_COLOR,
        borderRadius: { topLeft: BAR_RADIUS_PX, topRight: BAR_RADIUS_PX },
        borderSkipped: 'bottom',
        categoryPercentage: 1,
        barPercentage: 1,
        inflateAmount: -BAR_GAP_PX / 2
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => range(props.bins[items[0].dataIndex]),
            label: (item) => `${item.raw} ${item.raw === 1 ? 'turn' : 'turns'}`
          }
        }
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: AXIS_COLOR, maxRotation: 0, autoSkip: true, font: { size: 10 } } },
        y: { beginAtZero: true, grid: { color: GRID_COLOR }, ticks: { color: AXIS_COLOR, precision: 0, font: { size: 10 } } }
      }
    }
  })
}

onMounted(draw)
watch(() => props.bins, draw)
onBeforeUnmount(() => chart?.destroy())
</script>

<template>
  <canvas ref="canvas"></canvas>
</template>
