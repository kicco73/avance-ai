<script setup>
import { computed, ref } from 'vue'
import { useProjectSignals } from './useProjectSignals.js'
import InspectorSignalList from './InspectorSignalList.vue'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  projectId: { type: String, required: true },
  stateKey: { type: String, default: null },
  sessionId: { type: [Number, String], default: null }
})

const { signals, signalsLoading, loadSignals, refresh } = useProjectSignals(props)

const showOnlyRelevant = ref(true)

const displayedSignals = computed(() => {
  if (!showOnlyRelevant.value) return signals.value
  return signals.value.filter((s) => s.relevant)
})

defineExpose({ loadSignals, refresh })
</script>

<template>
  <div class="inspector-signals-section">
    <label v-if="!signalsLoading && signals.length" class="inspector-signals-relevant-toggle">
      <input type="checkbox" v-model="showOnlyRelevant" />
      Show only relevant signals
    </label>
    <p v-if="signalsLoading" class="signals-status">Loading…</p>
    <p v-else-if="!signals.length" class="signals-status">No signals defined.</p>
    <p v-else-if="!displayedSignals.length" class="signals-status">
      No relevant signals — none are computed in this state yet.
    </p>
    <InspectorSignalList v-else v-bind="$attrs" :signals="displayedSignals" />
  </div>
</template>

<style scoped>
.inspector-signals-section { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
.inspector-signals-relevant-toggle { display: flex; align-items: center; gap: 0.35rem; margin-bottom: 0.6rem; font-size: 0.78rem; color: #555; cursor: pointer; user-select: none; flex-shrink: 0; }
.inspector-signals-relevant-toggle input { cursor: pointer; }
.signals-status { margin: 0; color: #444; font-size: 0.9rem; }
</style>
