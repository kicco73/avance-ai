<script setup>
import { computed, ref, watch, nextTick } from 'vue'
import InspectorGraphTab from './InspectorGraphTab.vue'
import InspectorSignalsTab from './InspectorSignalsTab.vue'
import InspectorMetricsTab from './InspectorMetricsTab.vue'
import InspectorEnvTab from './InspectorEnvTab.vue'
import InspectorPerformanceTab from './InspectorPerformanceTab.vue'

const props = defineProps({
  projectName: { type: String, required: true },
  highlightedStateKey: { type: String, default: null },
  autoJumpOnHighlightChange: { type: Boolean, default: false },
  nextActionEdge: { type: Object, default: null },
  firedActionEdge: { type: Object, default: null },
  signalValues: { type: Object, default: () => ({}) },
  untilMessageId: { type: [Number, String], default: null },
  closable: { type: Boolean, default: true },
  editableFiles: { type: Array, default: null },
  annotatable: { type: Boolean, default: false },
  // Separate from `annotatable` (which only ever gates the States tab's
  // own expected-state control): the automaton's own starting point (see
  // BenchmarkProjectView.vue's own init-transition handling) has no real
  // signal evaluation behind it at all, so it can only ever be
  // disagreed-with on *where the automaton starts*, never on signal
  // values nothing was ever computed for — callers that don't
  // distinguish the two just pass the same value as `annotatable`.
  annotatableSignals: { type: Boolean, default: false },
  expectedState: { type: String, default: null },
  expectedValues: { type: Object, default: () => ({}) },
  showPerformanceTab: { type: Boolean, default: false },
  benchmarkSessionId: { type: [Number, String], default: null },
  // On by default (the "Edit project" view) — BenchmarkProjectView.vue
  // (the "Label sessions" view) turns it off: env is a live, per-user
  // memory the model builds up during actual chat, not something that
  // makes sense to inspect while reviewing a past labeled session.
  showEnvTab: { type: Boolean, default: true },
  // Whether the Env tab's stored values may be edited/deleted at the
  // current untilMessageId — the caller's call, not derivable in here:
  // EditProjectView.vue allows it both when nothing is selected (live)
  // and when the selected bubble is the conversation's own latest
  // message (still "now", nothing happened after it), but keeps every
  // earlier bubble read-only (see chat.env.Env.update — always writes
  // going forward from "now", there's no "editing history").
  envEditable: { type: Boolean, default: true }
})

const emit = defineEmits([
  'jump-to-definition', 'select-attachment', 'close', 'update-expected-state', 'update-expected-signals'
])

const inspectorTab = ref('graph')

// Which state the Signals tab's own "relevant" filter is scoped to (see
// InspectorSignalsTab.vue's stateKey prop) — a graph click overrides the
// default (props.highlightedStateKey, the live/current state) until the
// live context itself moves on (see the watch below), same as
// InspectorGraphTab.vue's own selectedElement already re-syncs itself
// whenever highlightedStateKey changes. jump-to-definition's own
// `stateKey` already means exactly this for both kinds it ever carries:
// the state itself when a node was tapped, or the state a tapped
// action's own edge originates *from* (see InspectorGraphTab.vue's
// edgeToCyData/matchStateKey) — never the destination, which is what
// makes this different from highlightedStateKey's own graph-highlight
// purpose.
const selectedStateForSignals = ref(null)
const relevantSignalsStateKey = computed(() => selectedStateForSignals.value ?? props.highlightedStateKey)

function handleJumpToDefinition(event) {
  selectedStateForSignals.value = event.stateKey
  emit('jump-to-definition', event)
}

const graphTabRef = ref(null)
const signalsTabRef = ref(null)
const metricsTabRef = ref(null)
const envTabRef = ref(null)
const performanceTabRef = ref(null)

async function refresh() {
  await Promise.all([
    signalsTabRef.value?.loadSignals(),
    graphTabRef.value?.loadGraph()
  ])
}

async function refreshMetrics() {
  if (inspectorTab.value === 'metrics') await metricsTabRef.value?.loadMetrics()
}

// Unlike refreshMetrics (heavier to compute, only refreshed while its own
// tab is open), env is cheap — just the latest DB row plus a few date/
// session lookups — and some of its computed values (time, datetime,
// current_session_duration_in_minutes, ...) are only ever correct as of
// "right now", so this always fetches, tab open or not, same as Signals'
// own unconditional refresh() above.
async function refreshEnv() {
  await envTabRef.value?.loadEnv()
}

async function refreshPerformance() {
  if (inspectorTab.value === 'performance') await performanceTabRef.value?.loadPerformanceMetrics()
}

function resize() {
  graphTabRef.value?.resize()
}

defineExpose({ refresh, refreshMetrics, refreshEnv, refreshPerformance, resize })

function setInspectorTab(tab) {
  inspectorTab.value = tab
  if (tab === 'graph') {
    nextTick(() => {
      graphTabRef.value?.resize()
      graphTabRef.value?.fit()
    })
  } else if (tab === 'metrics') {
    metricsTabRef.value?.loadMetrics()
  } else if (tab === 'env') {
    envTabRef.value?.loadEnv()
  } else if (tab === 'performance') {
    performanceTabRef.value?.loadPerformanceMetrics()
  }
}

watch(() => props.highlightedStateKey, () => {
  // The live context moved on (a new chat message/transition got
  // selected, or the conversation itself advanced) — drop any manual
  // graph-click override so the Signals tab's own relevance follows it
  // again, same as InspectorGraphTab.vue's own selectedElement already
  // re-syncs itself whenever this prop changes.
  selectedStateForSignals.value = null
})
</script>

<template>
  <div class="inspector-header">
    <span class="inspector-title">Inspector</span>
    <button v-if="closable" class="close-x-btn" title="Close" @click="emit('close')">×</button>
  </div>
  
  <div class="inspector-tabs">
    <button class="inspector-tab-btn" :class="{ 'inspector-tab-btn-active': inspectorTab === 'graph' }" @click="setInspectorTab('graph')">States</button>
    <button class="inspector-tab-btn" :class="{ 'inspector-tab-btn-active': inspectorTab === 'signals' }" @click="setInspectorTab('signals')">Signals</button>
    <button class="inspector-tab-btn" :class="{ 'inspector-tab-btn-active': inspectorTab === 'metrics' }" @click="setInspectorTab('metrics')">Metrics</button>
    <button v-if="showEnvTab" class="inspector-tab-btn" :class="{ 'inspector-tab-btn-active': inspectorTab === 'env' }" @click="setInspectorTab('env')">Env</button>
    <button v-if="showPerformanceTab" class="inspector-tab-btn" :class="{ 'inspector-tab-btn-active': inspectorTab === 'performance' }" @click="setInspectorTab('performance')">Performance</button>
  </div>

  <div class="inspector-body">
    <InspectorGraphTab 
      v-show="inspectorTab === 'graph'"
      ref="graphTabRef"
      :projectName="projectName"
      :highlightedStateKey="highlightedStateKey"
      :autoJumpOnHighlightChange="autoJumpOnHighlightChange"
      :nextActionEdge="nextActionEdge"
      :firedActionEdge="firedActionEdge"
      :editableFiles="editableFiles"
      :annotatable="annotatable"
      :expectedState="expectedState"
      @jump-to-definition="handleJumpToDefinition"
      @select-attachment="emit('select-attachment', $event)"
      @update-expected-state="emit('update-expected-state', $event)"
    />

    <InspectorSignalsTab
      v-show="inspectorTab === 'signals'"
      ref="signalsTabRef"
      :projectName="projectName"
      :signalValues="signalValues"
      :editableFiles="editableFiles"
      :annotatable="annotatableSignals"
      :expectedValues="expectedValues"
      :state-key="relevantSignalsStateKey"
      @jump-to-definition="emit('jump-to-definition', $event)"
      @select-attachment="emit('select-attachment', $event)"
      @update-expected-signals="emit('update-expected-signals', $event)"
    />

    <InspectorMetricsTab
      v-show="inspectorTab === 'metrics'"
      ref="metricsTabRef"
      :untilMessageId="untilMessageId"
    />

    <InspectorEnvTab
      v-if="showEnvTab"
      v-show="inspectorTab === 'env'"
      ref="envTabRef"
      :untilMessageId="untilMessageId"
      :editable="envEditable"
    />

    <InspectorPerformanceTab
      v-if="showPerformanceTab"
      v-show="inspectorTab === 'performance'"
      ref="performanceTabRef"
      :benchmarkSessionId="benchmarkSessionId"
    />
  </div>
</template>

<style scoped>
.inspector-header { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.5rem 0.75rem; background: #f5f5f7; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.inspector-title { font-size: 0.8rem; font-weight: 600; color: #555; text-transform: uppercase; letter-spacing: 0.03em; }
.inspector-tabs { display: flex; gap: 0.25rem; padding: 0.5rem 1rem 0; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.inspector-tab-btn { padding: 0.45rem 0.9rem; border: none; border-bottom: 2px solid transparent; border-radius: 0; background: none; cursor: pointer; font-size: 0.82rem; color: #666; }
.inspector-tab-btn:hover { color: #333; }
.inspector-tab-btn-active { color: #2c4d7a; font-weight: 600; border-bottom-color: #4a6fa5; }
.inspector-body { flex: 1; display: flex; flex-direction: column; min-height: 0; padding: 1rem; }
.close-x-btn { flex-shrink: 0; width: 1.4rem; height: 1.4rem; line-height: 1; border: none; border-radius: 6px; background: none; color: #666; cursor: pointer; font-size: 1rem; }
.close-x-btn:hover { background: #eee; }
</style>
