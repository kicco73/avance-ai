<script setup>
import { computed } from 'vue'

const props = defineProps({
  title: { type: String, default: '' },
  series: { type: Array, default: () => [] }
})

const values = computed(() => props.series.map((entry) => Number(entry.value) || 0))
const minValue = computed(() => (values.value.length ? Math.min(...values.value) : 0))
const maxValue = computed(() => (values.value.length ? Math.max(...values.value) : 0))

function widthFor(value) {
  const min = minValue.value
  const max = maxValue.value
  if (max === min) return '100%'
  return `${((value - min) / (max - min)) * 100}%`
}
</script>

<template>
  <div class="chart-dialog">
    <h2 v-if="title" class="chart-dialog-title">{{ title }}</h2>
    <div class="chart-dialog-bars">
      <div v-for="entry in series" :key="entry.line" class="chart-bar-row">
        <span class="chart-bar-label">{{ entry.line }}</span>
        <div class="chart-bar-track">
          <div class="chart-bar-fill" :style="{ width: widthFor(Number(entry.value) || 0) }"></div>
        </div>
        <span class="chart-bar-value">{{ entry.value }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chart-dialog {
  min-width: min(360px, 80vw);
}

.chart-dialog-title {
  margin: 0 0 1rem;
  padding-right: 1.6rem;
  font-size: 1.05rem;
  font-weight: 600;
  color: #333;
}

.chart-dialog-bars {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.chart-bar-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.chart-bar-label {
  flex: 0 0 30%;
  min-width: 0;
  font-size: 0.8rem;
  color: #555;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chart-bar-track {
  flex: 1;
  min-width: 40px;
  height: 8px;
  border-radius: 999px;
  background: #eee;
  overflow: hidden;
}

.chart-bar-fill {
  height: 100%;
  border-radius: 999px;
  background: #2e7d32;
  transition: width 0.3s ease;
}

.chart-bar-value {
  flex-shrink: 0;
  min-width: 2.5rem;
  text-align: right;
  font-size: 0.8rem;
  color: #555;
}
</style>
