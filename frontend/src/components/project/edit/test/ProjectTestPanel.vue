<script setup>
// "Test" mode's content, shown when EditProjectView.vue's `testOpen` is set.
// Two columns: TestsTree on the left (Sessions/States), a node's results on
// the right. Owns all data fetching/launching/polling — TestsTree itself
// (alongside TestNodeButton, both in this same test/ folder) stays purely presentational.
import { computed, onMounted, ref } from 'vue'
import TestsTree from './TestsTree.vue'
import SignalAccuracyDistributionChart from './SignalAccuracyDistributionChart.vue'
import DocInfoButton from '../../../DocInfoButton.vue'
import MetricDetail from '../../../inspector/MetricDetail.vue'
import { getTestMetrics, getProjectSignals, getProjectStates } from '../../../../api.js'
import { loadSessions, sessions, sessionsLoading } from '../../../../chatStore.js'
import { useResizablePanel } from '../../../../composables/useResizablePanel.js'
import { useFloatingMenu } from '../../../../composables/useFloatingMenu.js'
import { useTestExecutionTree } from '../../../../composables/useTestExecutionTree.js'

const props = defineProps({
  projectName: {
    type: String,
    required: true
  }
})

// Lets EditProjectView.vue mirror this panel's own selection into the
// Inspector's read-only Info tab — this stays the single source of truth
// for selectedNodeId, the parent just gets told when it changes.
const emit = defineEmits(['select'])

// Applies to whichever node gets activated next — one shared control, not a per-node choice.
const strategy = ref('batch')
const strategyLabels = { batch: 'Batch', turn_by_turn: 'Turn-by-turn' }

const {
  open: strategyOpen, triggerRef: strategyBtnEl, panelRef: strategyPanelEl, style: strategyPanelStyle,
  toggle: toggleStrategyMenu, close: closeStrategyMenu
} = useFloatingMenu()
const strategyLabel = computed(() => strategyLabels[strategy.value])

function selectStrategy(value) {
  strategy.value = value
  closeStrategyMenu()
}

const { width: treeWidth, startDrag: startTreeDrag } = useResizablePanel(280, { min: 200, max: 480 })

const projectStates = ref([])
const statesLoading = ref(false)

const projectSignals = ref([])
const signalsLoading = ref(false)

// name -> {ui_label, ui_description} for the fixed core-benchmark-metric
// registry (state_accuracy, signal_accuracy, ...) — every result row's own
// `name` below is one of these, resolved for display instead of the raw
// identifier. Loaded once; this registry is static per backend build, not
// per-project data.
const metricDefinitions = ref({})

async function loadMetricDefinitions() {
  try {
    const metrics = await getTestMetrics(props.projectName)
    metricDefinitions.value = Object.fromEntries(metrics.map((m) => [m.name, m]))
  } catch {
    // already surfaced via apiFetch
  }
}

function metricLabel(name) {
  return metricDefinitions.value[name]?.ui_label ?? name
}

function metricDescription(name) {
  return metricDefinitions.value[name]?.ui_description ?? null
}

function formatNumber(value) {
  return typeof value === 'number' ? value.toFixed(2) : '—'
}

const {
  tokensBurnt, nodeLastResult, selectedNodeId, selectedRun, selectedRunLoading,
  currentStrategyStatuses, currentStrategyProgress,
  rootStatus, rootBusy, rootButtonState, isHoveringRoot, showCancelRoot,
  selectedCacheKey, selectedNodeError, selectedNodeLabel, anyTestExecuted,
  onActivate, onAbort, onActivateRoot, onSelect,
  resettingCache, onResetCache,
} = useTestExecutionTree(props.projectName, strategy, sessions, projectSignals, emit)

onMounted(() => {
  loadSessions(true, props.projectName)
  loadMetricDefinitions()
  statesLoading.value = true
  getProjectStates(props.projectName).then((states) => {
    projectStates.value = states
  }).catch(() => {
    // already surfaced via apiFetch
  }).finally(() => {
    statesLoading.value = false
  })
  signalsLoading.value = true
  getProjectSignals(props.projectName).then(({ signals }) => {
    projectSignals.value = signals.map((entry) => entry.signal)
  }).catch(() => {
    // already surfaced via apiFetch
  }).finally(() => {
    signalsLoading.value = false
  })
})
</script>

<template>
  <div class="project-test-panel">
    <div class="tests-panel-tree" :style="{ width: treeWidth + 'px' }">
      <div class="tests-panel-strategy">
        <span class="tests-panel-tree-header">Test explorer</span>
        <div class="tests-panel-tree-actions">
          <button
            type="button"
            class="tests-panel-root-btn"
            :class="[`tests-panel-root-btn-${rootButtonState}`, { 'tests-panel-root-btn-cancel': showCancelRoot }]"
            :title="showCancelRoot ? 'Cancel' : rootStatus === 'pending' || rootStatus === 'ready' ? 'Queued…' : rootStatus === 'paused' ? 'Paused…' : rootStatus === 'running' ? 'Running…' : 'Run test'"
            @click="onActivateRoot"
            @mouseenter="isHoveringRoot = true"
            @mouseleave="isHoveringRoot = false"
          >
            <!-- Idle/ok/warning/fail: same chrome as the reset button next
                 to it (bordered square, plain icon) — the ring only earns
                 its place while something is actually in flight. -->
            <svg v-if="rootStatus === 'idle'" viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
              <path d="M8 5v14l11-7z" />
            </svg>
            <svg v-else-if="rootBusy" viewBox="0 0 24 24" width="16" height="16">
              <g class="tests-panel-root-btn-content" :class="{ 'tests-panel-root-btn-content-hidden': showCancelRoot }">
                <circle
                  class="tests-panel-root-btn-ring"
                  :class="{ 'tests-panel-root-btn-spinner-indeterminate': rootStatus !== 'running' }"
                  cx="12" cy="12" r="10" pathLength="100" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"
                  :stroke-dasharray="rootStatus === 'running' ? '8 100' : '50 100'"
                />
                <g v-if="rootStatus === 'paused'" transform="translate(12 12) scale(0.6) translate(-12 -12)">
                  <rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor" />
                  <rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor" />
                </g>
                <!-- 'running': a worker is inside root's own step right now
                     (root has exactly one — see RootAggregationJob._prepare)
                     — the lightning marks that instant, not "root is always
                     high-priority". Drawn inside the same ring as the spinner,
                     not swapped in place of it, so the ring stays visible. -->
                <path
                  v-else-if="rootStatus === 'running'"
                  class="tests-panel-root-btn-lightning"
                  d="M11 21v-8H7l6-11v8h4l-6 11z"
                  transform="translate(12 12) scale(0.75) translate(-12 -12)"
                  fill="currentColor"
                />
              </g>
              <path
                class="tests-panel-root-btn-cancel-icon"
                :class="{ 'tests-panel-root-btn-cancel-icon-visible': showCancelRoot }"
                d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"
                transform="translate(12 12) scale(0.6) translate(-12 -12)"
                fill="currentColor"
              />
            </svg>
            <svg v-else-if="rootStatus === 'ok'" viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline class="tests-panel-root-btn-check" points="4 13 9 18 20 6" pathLength="100" />
            </svg>
            <svg v-else-if="rootStatus === 'warning'" viewBox="0 0 24 24" width="10" height="10" fill="currentColor">
              <path d="M12 3L1 21h22L12 3zm0 5.5l6.6 11.5H5.4L12 8.5zM11 11v4h2v-4h-2zm0 5v2h2v-2h-2z" />
            </svg>
            <svg v-else-if="rootStatus === 'fail'" viewBox="0 0 24 24" width="10" height="10" fill="currentColor">
              <path d="M12 2a10 10 0 100 20 10 10 0 000-20zm3.5 13.1L15.1 16.5 12 13.4l-3.1 3.1-1.4-1.4L10.6 12 7.5 8.9l1.4-1.4L12 10.6l3.1-3.1 1.4 1.4L13.4 12z" />
            </svg>
            <svg v-else-if="rootStatus === 'aborted'" viewBox="0 0 24 24" width="10" height="10" fill="currentColor">
              <rect x="4" y="4" width="16" height="16" rx="2" />
            </svg>
          </button>
          <button
            class="tests-panel-reset-btn"
            title="Reset test cache"
            :disabled="resettingCache || !anyTestExecuted"
            @click="onResetCache"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
              <path d="M12 5V1L7 6l5 5V7c3.31 0 6 2.69 6 6s-2.69 6-6 6-6-2.69-6-6H4c0 4.42 3.58 8 8 8s8-3.58 8-8-3.58-8-8-8z" />
            </svg>
          </button>
        </div>
      </div>
      <p v-if="sessionsLoading || statesLoading || signalsLoading" class="tests-panel-tree-status">Loading…</p>
      <TestsTree
        v-else
        :sessions="sessions"
        :states="projectStates"
        :signals="projectSignals"
        :statuses="currentStrategyStatuses"
        :progresses="currentStrategyProgress"
        :selected-node-id="selectedNodeId"
        @select="onSelect"
        @activate="onActivate"
        @abort="onAbort"
      />
    </div>

    <div class="tests-panel-split-divider" @mousedown="startTreeDrag"></div>

    <div class="tests-panel-main">
      <div class="tests-panel-toolbar">
        <div class="tests-panel-toolbar-title">
          <span class="tests-panel-tokens-label">Tokens burnt: {{ tokensBurnt }}</span>
        </div>
        <div class="tests-panel-toolbar-actions">
          <div class="strategy-menu">
            <button ref="strategyBtnEl" class="strategy-btn" :title="strategyLabel" @click="toggleStrategyMenu">
              <span class="strategy-btn-label">{{ strategyLabel }}</span>
              <span class="strategy-btn-caret">▾</span>
            </button>
            <Teleport to="body">
              <div v-if="strategyOpen" ref="strategyPanelEl" class="model-panel" :style="strategyPanelStyle">
                <ul class="model-list">
                  <li v-for="(label, value) in strategyLabels" :key="value">
                    <button class="model-item" @click="selectStrategy(value)">
                      <span class="model-item-check">{{ strategy === value ? '✓' : '' }}</span>
                      <span class="model-item-label">{{ label }}</span>
                    </button>
                  </li>
                </ul>
              </div>
            </Teleport>
          </div>
        </div>
      </div>
      <div class="tests-panel-content">
      <p v-if="!selectedNodeId" class="tests-panel-placeholder">Select a node to see its results.</p>

      <template v-else-if="selectedNodeId === 'root'">
        <p class="tests-panel-placeholder">Aggregates not available in this product version.<br />Please select Sessions, States, Users, or Signals to see results.</p>
      </template>

      <template v-else-if="selectedNodeId.startsWith('session:')">
        <p v-if="selectedRunLoading" class="tests-panel-placeholder">Loading…</p>
        <p v-else-if="!selectedRun" class="tests-panel-placeholder">
          No test has been run for this session under this strategy yet.
        </p>
        <p v-else-if="selectedRun.status === 'failed'" class="tests-panel-error">{{ selectedRun.error || 'Test failed.' }}</p>
        <p v-else-if="selectedRun.status !== 'completed'" class="tests-panel-placeholder">Test {{ selectedRun.status }}…</p>
        <template v-else>
          <p v-if="selectedRun.error" class="tests-panel-error">{{ selectedRun.error }}</p>
          <p v-if="selectedRun.stale" class="tests-panel-stale-warning">
            The project has changed since this test ran — results may not reflect the current version.
          </p>
          <table v-if="selectedRun.results && selectedRun.results.length" class="tests-panel-metrics-table">
            <thead>
              <tr>
                <th><span class="tests-panel-metric-header">Metric<DocInfoButton doc-name="benchmark" title="Benchmark" /></span></th><th>Std dev</th><th>Samples</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="metric in selectedRun.results" :key="metric.name" :class="{ 'tests-panel-row-empty': !metric.sample_count }">
                <td><MetricDetail :label="metricLabel(metric.name)" :description="metricDescription(metric.name)" :value="metric.value" :median="metric.median" /></td>
                <td>{{ formatNumber(metric.standard_deviation) }}</td>
                <td>{{ metric.sample_count }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>

      <template v-else-if="selectedNodeId === 'sessions-branch' || selectedNodeId.startsWith('user:') || selectedNodeId === 'users-branch'">
        <p v-if="selectedNodeError" class="tests-panel-error">{{ selectedNodeError }}</p>
        <p v-else-if="!nodeLastResult[selectedCacheKey] || !nodeLastResult[selectedCacheKey].length" class="tests-panel-placeholder">
          No test has been run for {{ selectedNodeId.startsWith('user:') ? 'this user' : selectedNodeId === 'users-branch' ? 'the users aggregation' : 'the whole project' }} under this strategy yet.
        </p>
        <table v-else class="tests-panel-metrics-table">
          <thead>
            <tr>
              <th><span class="tests-panel-metric-header">Metric<DocInfoButton doc-name="benchmark" title="Benchmark" /></span></th><th>Std dev</th><th>Samples</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="metric in nodeLastResult[selectedCacheKey]" :key="metric.name"
              :class="{ 'tests-panel-row-empty': !metric.sample_count }"
            >
              <td><MetricDetail :label="metricLabel(metric.name)" :description="metricDescription(metric.name)" :value="metric.value" :median="metric.median" /></td>
              <td>{{ formatNumber(metric.standard_deviation) }}</td>
              <td>{{ metric.sample_count }}</td>
            </tr>
          </tbody>
        </table>
      </template>

      <template v-else-if="selectedNodeId.startsWith('state:')">
        <p v-if="selectedNodeError" class="tests-panel-error">{{ selectedNodeError }}</p>
        <p v-else-if="!nodeLastResult[selectedCacheKey]" class="tests-panel-placeholder">
          No test has been run for this state under this strategy yet.
        </p>
        <template v-else>
          <div class="tests-panel-distribution-block">
            <SignalAccuracyDistributionChart :distribution="nodeLastResult[selectedCacheKey].distribution" />
          </div>
          <table class="tests-panel-metrics-table">
            <thead>
              <tr>
                <th><span class="tests-panel-metric-header">Metric<DocInfoButton doc-name="benchmark" title="Benchmark" /></span></th><th>Std dev</th><th>Samples</th>
              </tr>
            </thead>
            <tbody>
              <tr :class="{ 'tests-panel-row-empty': !nodeLastResult[selectedCacheKey].sample_count }">
                <td><MetricDetail :label="metricLabel(nodeLastResult[selectedCacheKey].name)" :description="metricDescription(nodeLastResult[selectedCacheKey].name)" :value="nodeLastResult[selectedCacheKey].value" :median="nodeLastResult[selectedCacheKey].median" /></td>
                <td>{{ formatNumber(nodeLastResult[selectedCacheKey].standard_deviation) }}</td>
                <td>{{ nodeLastResult[selectedCacheKey].sample_count }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>

      <template v-else-if="selectedNodeId.startsWith('signal:')">
        <p v-if="selectedNodeError" class="tests-panel-error">{{ selectedNodeError }}</p>
        <p v-else-if="!nodeLastResult[selectedCacheKey]" class="tests-panel-placeholder">
          No test has been run for this signal under this strategy yet.
        </p>
        <template v-else>
          <div class="tests-panel-distribution-block">
            <SignalAccuracyDistributionChart :distribution="nodeLastResult[selectedCacheKey].distribution" />
          </div>
          <table class="tests-panel-metrics-table">
            <thead>
              <tr>
                <th><span class="tests-panel-metric-header">Metric<DocInfoButton doc-name="benchmark" title="Benchmark" /></span></th><th>Std dev</th><th>Samples</th>
              </tr>
            </thead>
            <tbody>
              <tr :class="{ 'tests-panel-row-empty': !nodeLastResult[selectedCacheKey].sample_count }">
                <td>
                  <MetricDetail
                    :label="metricLabel('signal_accuracy')" :description="metricDescription('signal_accuracy')"
                    :value="nodeLastResult[selectedCacheKey].value" :median="nodeLastResult[selectedCacheKey].median"
                  />
                </td>
                <td>{{ formatNumber(nodeLastResult[selectedCacheKey].standard_deviation) }}</td>
                <td>{{ nodeLastResult[selectedCacheKey].sample_count }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>

      <template v-else-if="selectedNodeId === 'signals-branch'">
        <p v-if="selectedNodeError" class="tests-panel-error">{{ selectedNodeError }}</p>
        <p v-else-if="!nodeLastResult[selectedCacheKey]" class="tests-panel-placeholder">
          No signal test has been run under this strategy yet.
        </p>
        <template v-else>
          <div class="tests-panel-distribution-block">
            <SignalAccuracyDistributionChart :distribution="nodeLastResult[selectedCacheKey].distribution" />
          </div>
          <table class="tests-panel-metrics-table">
            <thead>
              <tr><th><span class="tests-panel-metric-header">Metric<DocInfoButton doc-name="benchmark" title="Benchmark" /></span></th><th>Samples</th></tr>
            </thead>
            <tbody>
              <tr :class="{ 'tests-panel-row-empty': !nodeLastResult[selectedCacheKey].sample_count }">
                <td><MetricDetail label="Overall" :value="nodeLastResult[selectedCacheKey].value" :median="nodeLastResult[selectedCacheKey].median" /></td>
                <td>{{ nodeLastResult[selectedCacheKey].sample_count }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>

      <template v-else-if="selectedNodeId === 'states-branch'">
        <p v-if="selectedNodeError" class="tests-panel-error">{{ selectedNodeError }}</p>
        <p v-else-if="!nodeLastResult[selectedCacheKey]" class="tests-panel-placeholder">
          No state test has been run under this strategy yet.
        </p>
        <template v-else>
          <div class="tests-panel-distribution-block">
            <SignalAccuracyDistributionChart :distribution="nodeLastResult[selectedCacheKey].distribution" />
          </div>
          <table class="tests-panel-metrics-table">
            <thead>
              <tr><th><span class="tests-panel-metric-header">Metric<DocInfoButton doc-name="benchmark" title="Benchmark" /></span></th><th>Samples</th></tr>
            </thead>
            <tbody>
              <tr :class="{ 'tests-panel-row-empty': !nodeLastResult[selectedCacheKey].sample_count }">
                <td><MetricDetail label="Overall" :value="nodeLastResult[selectedCacheKey].value" :median="nodeLastResult[selectedCacheKey].median" /></td>
                <td>{{ nodeLastResult[selectedCacheKey].sample_count }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>

      <p v-else class="tests-panel-placeholder">{{ selectedNodeLabel }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.project-test-panel {
  flex: 1;
  display: flex;
  flex-direction: row;
  min-height: 0;
  min-width: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.tests-panel-tree {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-right: 1px solid #ddd;
  background: #f9fafb;
}

.tests-panel-split-divider {
  flex-shrink: 0;
  width: 6px;
  border-radius: 3px;
  background: transparent;
  cursor: col-resize;
}
.tests-panel-split-divider:hover {
  background: #dbe4f0;
}

.tests-panel-strategy {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid #ddd;
}

.tests-panel-tree-header {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.tests-panel-tree-actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.tests-panel-root-btn,
.tests-panel-reset-btn {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  border-radius: 6px;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  padding: 0;
}

.tests-panel-root-btn {
  transition: background-color 0.25s ease, color 0.25s ease, border-color 0.25s ease;
}

.tests-panel-root-btn,
.tests-panel-reset-btn {
  border: 1px solid #4a6fa5;
}

.tests-panel-root-btn:hover:not(:disabled):not(.tests-panel-root-btn-cancel),
.tests-panel-reset-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.tests-panel-root-btn:disabled,
.tests-panel-reset-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.tests-panel-root-btn-ready {
  border-color: transparent;
  background: none;
  color: #4a6fa5;
}

.tests-panel-root-btn-running {
  border-color: transparent;
  background: none;
  color: #2e7d32;
}

.tests-panel-root-btn-lightning {
  animation: tests-panel-root-btn-glow 0.9s ease-in-out infinite alternate;
}

@keyframes tests-panel-root-btn-glow {
  from { opacity: 1; }
  to { opacity: 0.3; }
}

.tests-panel-root-btn-ring {
  transform-origin: center;
  transform: rotate(-90deg);
  transition: stroke-dasharray 0.2s linear;
}

.tests-panel-root-btn-ring.tests-panel-root-btn-spinner-indeterminate {
  animation: tests-panel-root-btn-spin 0.9s linear infinite;
}

@keyframes tests-panel-root-btn-spin {
  from { transform: rotate(-90deg); }
  to { transform: rotate(270deg); }
}

.tests-panel-root-btn-ok {
  border-color: #2e7d32;
  color: #2e7d32;
}

.tests-panel-root-btn-check {
  stroke-dasharray: 100;
  stroke-dashoffset: 100;
  animation: tests-panel-root-btn-check-draw 0.4s ease-out forwards;
}

@keyframes tests-panel-root-btn-check-draw {
  to { stroke-dashoffset: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .tests-panel-root-btn-check {
    animation: none;
    stroke-dashoffset: 0;
  }
}

.tests-panel-root-btn-ok:hover:not(:disabled) {
  background: #2e7d32;
  color: white;
}

.tests-panel-root-btn-warning {
  color: #b26a00;
}

.tests-panel-root-btn-warning:hover:not(:disabled) {
  background: #b26a00;
  color: white;
}

.tests-panel-root-btn-fail {
  color: #c62828;
}

.tests-panel-root-btn-fail:hover:not(:disabled) {
  background: #c62828;
  color: white;
}

.tests-panel-root-btn-aborted {
  color: #757575;
}

.tests-panel-root-btn-aborted:hover:not(:disabled) {
  background: #757575;
  color: white;
}

.tests-panel-root-btn-cancel {
  border-color: transparent;
  background: #c62828;
  color: white;
}

.tests-panel-root-btn-content {
  opacity: 1;
  transition: opacity 0.25s ease;
}

.tests-panel-root-btn-content-hidden {
  opacity: 0;
}

.tests-panel-root-btn-cancel-icon {
  opacity: 0;
  transition: opacity 0.25s ease;
}

.tests-panel-root-btn-cancel-icon-visible {
  opacity: 1;
}

.tests-panel-tokens-label {
  font-size: 0.8rem;
  color: #555;
  white-space: nowrap;
}

.strategy-menu {
  position: relative;
  display: flex;
  align-items: center;
}

.strategy-btn {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  max-width: 160px;
}

.strategy-btn-label {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.strategy-btn-caret {
  flex: none;
  font-size: 0.65rem;
}

.strategy-btn:hover {
  background: #4a6fa5;
  color: white;
}

.tests-panel-tree-status {
  padding: 0.75rem 0.9rem;
  font-size: 0.85rem;
  color: #666;
}

.tests-panel-main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.tests-panel-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #f5f5f7;
  border-bottom: 1px solid #ddd;
  flex-shrink: 0;
}

.tests-panel-toolbar-title {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.tests-panel-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.tests-panel-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 1rem;
}

.tests-panel-distribution-block {
  width: 100%;
  height: 200px;
  max-height: 200px;
  margin-bottom: 1rem;
}

.tests-panel-metric-header {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}

.tests-panel-placeholder {
  color: #666;
}

.tests-panel-stale-warning {
  margin: 0 0 0.75rem;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  background: #fff4e0;
  border: 1px solid #f0c674;
  color: #7a5300;
  font-size: 0.82rem;
}

.tests-panel-error {
  margin: 0 0 0.75rem;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  background: #fde8e8;
  border: 1px solid #f0a8a8;
  color: #8a1f1f;
  font-size: 0.82rem;
  white-space: pre-wrap;
  word-break: break-word;
}

.tests-panel-metrics-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
}

.tests-panel-metrics-table th,
.tests-panel-metrics-table td {
  text-align: left;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid #eee;
}

.tests-panel-metrics-table th {
  color: #555;
  font-weight: 600;
  text-transform: uppercase;
  font-size: 0.7rem;
  letter-spacing: 0.03em;
}

.tests-panel-overall-row td {
  font-weight: 600;
  border-top: 2px solid #ccc;
}

.tests-panel-row-empty td {
  color: #aaa;
}
</style>
