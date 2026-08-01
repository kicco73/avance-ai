<script setup>
import { ref, watch, nextTick } from 'vue'
import InspectorGraphTab from './InspectorGraphTab.vue'
import InspectorSignalsTab from './InspectorSignalsTab.vue'
import InspectorMetricsTab from './InspectorMetricsTab.vue'
import InspectorPerformanceTab from './InspectorPerformanceTab.vue'
import InspectorModelTab from './InspectorModelTab.vue'

const props = defineProps({
  projectName: { type: String, required: true },
  highlightedStateKey: { type: String, default: null },
  autoJumpOnHighlightChange: { type: Boolean, default: false },
  nextActionEdge: { type: Object, default: null },
  firedActionEdge: { type: Object, default: null },
  signalValues: { type: Object, default: () => ({}) },
  untilMessageId: { type: [Number, String], default: null },
  showModelTab: { type: Boolean, default: false },
  activeModel: { type: Object, default: null },
  closable: { type: Boolean, default: true },
  editableFiles: { type: Array, default: null },
  annotatable: { type: Boolean, default: false },
  expectedState: { type: String, default: null },
  expectedValues: { type: Object, default: () => ({}) },
  showPerformanceTab: { type: Boolean, default: false },
  benchmarkSessionId: { type: [Number, String], default: null }
})

const emit = defineEmits([
  'jump-to-definition', 'select-attachment', 'close', 'update-expected-state', 'update-expected-signals'
])

const inspectorTab = ref('graph')

const graphTabRef = ref(null)
const signalsTabRef = ref(null)
const metricsTabRef = ref(null)
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

async function refreshPerformance() {
  if (inspectorTab.value === 'performance') await performanceTabRef.value?.loadPerformanceMetrics()
}

function resize() {
  graphTabRef.value?.resize()
}

defineExpose({ refresh, refreshMetrics, refreshPerformance, resize })

function setInspectorTab(tab) {
  inspectorTab.value = tab
  if (tab === 'graph') {
    nextTick(() => {
      graphTabRef.value?.resize()
      graphTabRef.value?.fit()
    })
  } else if (tab === 'metrics') {
    metricsTabRef.value?.loadMetrics()
  } else if (tab === 'performance') {
    performanceTabRef.value?.loadPerformanceMetrics()
  }
}

watch(() => props.benchmarkSessionId, () => {
  if (inspectorTab.value === 'performance') performanceTabRef.value?.loadPerformanceMetrics()
})
watch(() => props.highlightedStateKey, () => {
  // Sync tab internal states if needed, but handled inside the tab
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
    <button v-if="showPerformanceTab" class="inspector-tab-btn" :class="{ 'inspector-tab-btn-active': inspectorTab === 'performance' }" @click="setInspectorTab('performance')">Performance</button>
    <button v-if="showModelTab" class="inspector-tab-btn" :class="{ 'inspector-tab-btn-active': inspectorTab === 'model' }" @click="setInspectorTab('model')">Model</button>
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
      @jump-to-definition="emit('jump-to-definition', $event)"
      @select-attachment="emit('select-attachment', $event)"
      @update-expected-state="emit('update-expected-state', $event)"
    />

    <InspectorSignalsTab 
      v-show="inspectorTab === 'signals'"
      ref="signalsTabRef"
      :projectName="projectName"
      :signalValues="signalValues"
      :editableFiles="editableFiles"
      :annotatable="annotatable"
      :expectedValues="expectedValues"
      @jump-to-definition="emit('jump-to-definition', $event)"
      @select-attachment="emit('select-attachment', $event)"
      @update-expected-signals="emit('update-expected-signals', $event)"
    />

    <InspectorMetricsTab 
      v-show="inspectorTab === 'metrics'"
      ref="metricsTabRef"
      :untilMessageId="untilMessageId"
    />

    <InspectorPerformanceTab 
      v-if="showPerformanceTab"
      v-show="inspectorTab === 'performance'"
      ref="performanceTabRef"
      :benchmarkSessionId="benchmarkSessionId"
    />

    <InspectorModelTab 
      v-if="showModelTab"
      v-show="inspectorTab === 'model'"
      :activeModel="activeModel"
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
