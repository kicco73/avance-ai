<script setup>
import { onMounted, ref } from 'vue'
import { getSignals, postTriggersPreview } from '../api.js'

defineProps({
  state: {
    type: Object,
    default: null
  },
  autoTrackingEnabled: {
    type: Boolean,
    default: false
  },
  autoTrackingLoading: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['close', 'toggle-auto-tracking'])

const loading = ref(true)
const signals = ref([])

const triggersLoading = ref(false)
const triggers = ref([])

async function load() {
  loading.value = true
  let signalsOk = true
  try {
    signals.value = await getSignals()
  } catch {
    // already surfaced via apiFetch
    signalsOk = false
  } finally {
    loading.value = false
  }

  if (signalsOk) await loadTriggers()
}

// Reuses the signal values already fetched above — never calls the AI again.
async function loadTriggers() {
  triggersLoading.value = true
  try {
    const signalValues = Object.fromEntries(
      signals.value.map((s) => [s.name, s.error ? null : s.value])
    )
    triggers.value = await postTriggersPreview(signalValues)
  } catch {
    // already surfaced via apiFetch
  } finally {
    triggersLoading.value = false
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
      <div class="signals-header-actions">
        <button
          class="autotracking-btn"
          :class="{ 'autotracking-btn-on': autoTrackingEnabled }"
          :disabled="autoTrackingLoading"
          @click="emit('toggle-auto-tracking')"
        >
          Auto-tracking: {{ autoTrackingEnabled ? 'On' : 'Off' }}
        </button>
        <button class="close-btn" @click="emit('close')">Back</button>
      </div>
    </div>

    <div class="signals-body">
      <p v-if="loading" class="signals-status">Loading signals…</p>

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

      <div v-if="!loading" class="triggers-section">
        <h2 class="state-name-title">{{ state?.label ?? '—' }}</h2>
        <p v-if="state?.description" class="triggers-state-description">
          {{ state.description }}
        </p>

        <h3 class="triggers-heading">Next triggerable action</h3>

        <p v-if="triggersLoading" class="signals-status">Evaluating triggers…</p>
        <p v-else-if="!triggers.length" class="signals-status">
          No triggerable actions defined for the current state.
        </p>

        <div v-else class="triggers-list">
          <div
            v-for="t in triggers"
            :key="t.action_name"
            class="trigger-row"
            :class="{ 'trigger-row-winner': t.would_fire }"
          >
            <div class="trigger-info">
              <span class="trigger-action-name">
                {{ t.action_name }}
                <span v-if="t.would_fire" class="trigger-winner-badge">would fire next</span>
              </span>
              <code class="trigger-expression">{{ t.trigger }}</code>
              <span class="trigger-target">→ {{ t.target }}</span>
            </div>
            <span class="trigger-result" :class="t.result ? 'trigger-result-true' : 'trigger-result-false'">
              {{ t.result ? 'true' : 'false' }}
            </span>
          </div>
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

.signals-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.autotracking-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #999;
  background: white;
  color: #666;
  cursor: pointer;
}

.autotracking-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.autotracking-btn-on {
  border-color: #2e7d32;
  background: #2e7d32;
  color: white;
}

.autotracking-btn-on:hover:not(:disabled) {
  background: #256428;
}

.autotracking-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
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

.triggers-section {
  max-width: 640px;
  margin: 2.5rem auto 0;
  padding-top: 1.5rem;
  border-top: 1px solid #ddd;
}

.state-name-title {
  margin: 0 0 0.4rem;
  font-size: 1.3rem;
}

.triggers-state-description {
  margin: 0 0 1.75rem;
  font-size: 0.9rem;
  color: #555;
  line-height: 1.45;
}

.triggers-heading {
  margin: 0 0 0.75rem;
  font-size: 0.95rem;
  color: #444;
}

.triggers-list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.trigger-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.6rem 0.75rem;
  border-radius: 8px;
  border: 1px solid #eee;
  background: #fafafa;
}

.trigger-row-winner {
  border-color: #2e7d32;
  background: #eef7ee;
}

.trigger-info {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  min-width: 0;
}

.trigger-action-name {
  font-weight: 600;
  font-size: 0.85rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.trigger-winner-badge {
  font-weight: 600;
  font-size: 0.7rem;
  color: white;
  background: #2e7d32;
  border-radius: 999px;
  padding: 0.1rem 0.5rem;
}

.trigger-expression {
  font-size: 0.78rem;
  color: #555;
  background: #eee;
  border-radius: 4px;
  padding: 0.1rem 0.4rem;
  width: fit-content;
}

.trigger-target {
  font-size: 0.75rem;
  color: #999;
}

.trigger-result {
  flex: none;
  font-size: 0.8rem;
  font-weight: 600;
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
}

.trigger-result-true {
  color: #2e7d32;
  background: #e3f2e3;
}

.trigger-result-false {
  color: #999;
  background: #eee;
}
</style>
