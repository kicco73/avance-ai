<script setup>
import { useProjectSignals } from '../../inspector/useProjectSignals.js'
import InspectorSignalList from '../../inspector/InspectorSignalList.vue'
import SegmentedControl from '../../../../../components/skillkit/SegmentedControl.vue'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  projectId: { type: String, required: true },
  stateKey: { type: String, default: null },
  stateData: { type: Object, default: null }
})

const emit = defineEmits(['set-state-field', 'add-signal'])

const SIGNAL_TRACKING_STRATEGIES = [
  { id: 'relevant', label: 'Relevant', title: "Only the signals this state's own actions read" },
  { id: 'all', label: 'All signals', title: 'Every declared signal, whether or not this state reads it' }
]

const { signals, signalsLoading, loadSignals, refresh } = useProjectSignals(props)

defineExpose({ loadSignals, refresh })
</script>

<template>
  <div class="inspector-signals-section">
    <div v-if="stateKey != null && stateData" class="design-signals-strategy">
      <span class="design-signals-strategy-label">Tracking</span>
      <SegmentedControl
        :model-value="stateData.signalTrackingStrategy"
        :options="SIGNAL_TRACKING_STRATEGIES"
        @update:model-value="(strategy) => emit('set-state-field', 'signal-tracking-strategy', strategy)"
      />
    </div>
    <p v-if="signalsLoading" class="signals-status">Loading…</p>
    <p v-else-if="!signals.length" class="signals-status">No signals defined.</p>
    <InspectorSignalList v-else v-bind="$attrs" :signals="signals" />
    <button class="inspector-signals-add-btn" @click="emit('add-signal')">+ Add signal</button>
  </div>
</template>

<style scoped>
.inspector-signals-section { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
.design-signals-strategy { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; margin-bottom: 0.6rem; flex-shrink: 0; }
.design-signals-strategy-label { font-size: 0.78rem; color: #555; }
.signals-status { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-signals-add-btn { flex-shrink: 0; margin-top: 0.5rem; padding: 0.5rem; border-radius: 6px; border: 1px dashed #4a6fa5; background: white; color: #4a6fa5; font-size: 0.82rem; cursor: pointer; }
.inspector-signals-add-btn:hover { background: #eef2f9; }
</style>
