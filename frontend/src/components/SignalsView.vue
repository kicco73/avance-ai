<script setup>
import { onMounted, ref } from 'vue'
import { getSignals } from '../api.js'

const emit = defineEmits(['close'])

const loading = ref(true)
const error = ref('')
const signals = ref([])

async function load() {
  loading.value = true
  error.value = ''
  try {
    signals.value = await getSignals()
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function hasValue(signal) {
  return signal.value !== null && !signal.error
}

onMounted(load)
</script>

<template>
  <div class="signals-overlay">
    <div class="signals-header">
      <h2>Signals</h2>
      <button class="close-btn" @click="emit('close')">Back</button>
    </div>

    <div class="signals-body">
      <p v-if="loading" class="signals-status">Loading signals…</p>
      <p v-else-if="error" class="signals-status signals-error">{{ error }}</p>

      <div v-else class="signals-chart">
        <div
          v-for="signal in signals"
          :key="signal.name"
          class="signal-row"
          :title="signal.description"
        >
          <div class="signal-label">
            <span class="signal-title">{{ signal.ui_label }}</span>
            <span class="signal-description">{{ signal.description }}</span>
          </div>

          <div class="signal-bar-track">
            <div
              v-if="hasValue(signal)"
              class="signal-bar-fill"
              :style="{ width: signal.value + '%' }"
            ></div>
            <div v-else class="signal-bar-fill signal-bar-na"></div>
          </div>

          <span class="signal-value" :class="{ 'signal-value-na': !hasValue(signal) }">
            {{ hasValue(signal) ? signal.value : 'n/a' }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.signals-overlay {
  position: fixed;
  inset: 0;
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.signals-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
}

.signals-header h2 {
  margin: 0;
  font-size: 1.1rem;
}

.close-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.close-btn:hover {
  background: #4a6fa5;
  color: white;
}

.signals-body {
  flex: 1;
  overflow-y: auto;
  padding: 1.5rem 1rem;
}

.signals-status {
  color: #444;
}

.signals-error {
  color: #c62828;
}

.signals-chart {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  max-width: 640px;
  margin: 0 auto;
}

.signal-row {
  display: grid;
  grid-template-columns: minmax(120px, 200px) 1fr 40px;
  align-items: center;
  gap: 0.75rem;
}

.signal-label {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.signal-title {
  font-weight: 600;
  font-size: 0.9rem;
}

.signal-description {
  font-size: 0.75rem;
  color: #777;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.signal-bar-track {
  height: 14px;
  border-radius: 999px;
  background: #eee;
  overflow: hidden;
}

.signal-bar-fill {
  height: 100%;
  background: #4a6fa5;
  border-radius: 999px;
  transition: width 0.3s ease;
}

.signal-bar-na {
  width: 100%;
  background: repeating-linear-gradient(45deg, #ccc, #ccc 6px, #ddd 6px, #ddd 12px);
}

.signal-value {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-size: 0.85rem;
  color: #444;
}

.signal-value-na {
  color: #999;
  font-style: italic;
}
</style>
