<script setup>
// "Auto" mode's content, shown when EditProjectView.vue's `autoOpen` is set.
// Two columns: TestsTree on the left (Sessions/States), a node's results on
// the right. Owns all data fetching/launching/polling — TestsTree itself
// (alongside TestNodeButton, both in this same auto/ folder) stays purely presentational.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import TestsTree from './TestsTree.vue'
import {
  getBenchmarkRun, getBenchmarkRuns, getProjectStates, getStateJob, postBenchmarkRun, postStateTest
} from '../../../../api.js'
import { loadSessions, sessions, sessionsLoading } from '../../../../chatStore.js'

const props = defineProps({
  projectName: {
    type: String,
    required: true
  }
})

// Applies to whichever node gets activated next — one shared control, not a per-node choice.
const strategy = ref('turn_by_turn')

const projectStates = ref([])
const statesLoading = ref(false)

// Every cache below is keyed by `${strategy}:${nodeId}`, never nodeId
// alone — turn_by_turn and batch results aren't comparable, so switching
// strategy must never show the other strategy's cached status/result for
// the same node.
function cacheKey(strategyName, nodeId) {
  return `${strategyName}:${nodeId}`
}

// { [cacheKey]: 'idle'|'running'|'ok'|'warning'|'fail' } — idle is TestsTree's
// own implicit default for anything missing here.
const nodeStatuses = ref({})
// A state node's own most recent job result — Signal Accuracy only, kept
// purely client-side (no server-side history for an ephemeral job, see
// backend jobs/job_sink.py's InMemoryJobSink), lost on page refresh.
const nodeLastResult = ref({})
// A state node's own most recent job failure message, alongside
// nodeLastResult above (null result on failure must stay distinguishable
// from "never run").
const nodeError = ref({})
// cacheKey -> pending setTimeout id, plain bookkeeping (never rendered).
const pollTimers = {}

const selectedNodeId = ref(null)
const selectedRun = ref(null)
const selectedRunLoading = ref(false)

// TestsTree only ever sees the active strategy's own statuses — a node
// with no entry here falls back to its own 'idle' default.
const currentStrategyStatuses = computed(() => {
  const prefix = `${strategy.value}:`
  const result = {}
  for (const [key, status] of Object.entries(nodeStatuses.value)) {
    if (key.startsWith(prefix)) result[key.slice(prefix.length)] = status
  }
  return result
})

const selectedCacheKey = computed(() => (
  selectedNodeId.value ? cacheKey(strategy.value, selectedNodeId.value) : null
))

function clearPoll(key) {
  if (pollTimers[key] != null) {
    clearTimeout(pollTimers[key])
    delete pollTimers[key]
  }
}

function setStatus(key, status) {
  nodeStatuses.value = { ...nodeStatuses.value, [key]: status }
}

// completed with no error -> ok; completed but error carries text (one
// or more sessions skipped, e.g. no known starting state) -> warning,
// never a threshold on the metrics themselves. failed -> fail.
function statusFromOutcome(status, error) {
  if (status === 'failed') return 'fail'
  if (status === 'completed') return error ? 'warning' : 'ok'
  return 'running'
}

function pollSessionRun(nodeId, runStrategy, key, runId) {
  clearPoll(key)
  const tick = async () => {
    let run = null
    try {
      run = await getBenchmarkRun(props.projectName, runId)
    } catch {
      // already surfaced via apiFetch — keep polling, a transient
      // network hiccup shouldn't drop this node's own status tracking.
      pollTimers[key] = setTimeout(tick, 1000)
      return
    }
    if (run.status === 'pending' || run.status === 'running') {
      pollTimers[key] = setTimeout(tick, 1000)
      return
    }
    setStatus(key, statusFromOutcome(run.status, run.error))
    if (selectedNodeId.value === nodeId && strategy.value === runStrategy) selectedRun.value = run
  }
  tick()
}

function pollStateJob(key, jobId) {
  clearPoll(key)
  const tick = async () => {
    let job = null
    try {
      job = await getStateJob(props.projectName, jobId)
    } catch {
      pollTimers[key] = setTimeout(tick, 1000)
      return
    }
    if (job == null || job.status === 'pending' || job.status === 'running') {
      pollTimers[key] = setTimeout(tick, 1000)
      return
    }
    setStatus(key, statusFromOutcome(job.status, job.error))
    nodeError.value = { ...nodeError.value, [key]: job.status === 'failed' ? job.error : null }
    nodeLastResult.value = {
      ...nodeLastResult.value,
      [key]: job.result ? JSON.parse(job.result) : null
    }
  }
  tick()
}

async function onActivate(nodeId) {
  // Pressing play selects the node it belongs to, same as clicking its
  // row — the results panel should already be pointed at it once the
  // run/job finishes.
  onSelect(nodeId)
  // Snapshot the strategy at launch time — the job is dispatched under
  // it regardless of whether the dropdown changes before it finishes.
  const activeStrategy = strategy.value
  const key = cacheKey(activeStrategy, nodeId)
  setStatus(key, 'running')
  try {
    if (nodeId.startsWith('session:')) {
      const sessionId = Number(nodeId.slice('session:'.length))
      const run = await postBenchmarkRun(props.projectName, sessionId, activeStrategy)
      pollSessionRun(nodeId, activeStrategy, key, run.id)
    } else if (nodeId.startsWith('state:')) {
      const stateKey = nodeId.slice('state:'.length)
      const { job_id } = await postStateTest(props.projectName, stateKey, activeStrategy)
      pollStateJob(key, job_id)
    }
  } catch {
    // already surfaced via apiFetch
    setStatus(key, 'fail')
  }
}

async function loadSelectedSessionRun(nodeId) {
  const sessionId = Number(nodeId.slice('session:'.length))
  selectedRunLoading.value = true
  try {
    const runs = await getBenchmarkRuns(props.projectName, sessionId)
    // Already most-recent-first (see backend BenchmarkRunService.list_runs)
    // — filtered to the active strategy, since turn_by_turn and batch
    // runs aren't comparable and must never be shown as if they were.
    selectedRun.value = runs.find((run) => run.strategy === strategy.value) ?? null
  } catch {
    selectedRun.value = null
  } finally {
    selectedRunLoading.value = false
  }
}

async function onSelect(nodeId) {
  selectedNodeId.value = nodeId
  selectedRun.value = null
  if (!nodeId.startsWith('session:')) return
  await loadSelectedSessionRun(nodeId)
}

// Switching strategy must refresh whatever's on screen for the currently
// selected session — otherwise it would keep showing the other
// strategy's last-fetched run.
watch(strategy, () => {
  if (selectedNodeId.value && selectedNodeId.value.startsWith('session:')) {
    loadSelectedSessionRun(selectedNodeId.value)
  }
})

const selectedNodeLabel = computed(() => {
  const nodeId = selectedNodeId.value
  if (!nodeId) return ''
  if (nodeId === 'root') return props.projectName
  if (nodeId === 'sessions-branch') return 'Sessions'
  if (nodeId === 'states-branch') return 'Stats'
  if (nodeId.startsWith('session:')) {
    const id = Number(nodeId.slice('session:'.length))
    const session = sessions.value.find((s) => s.id === id)
    return session ? (session.title || session.end_state || `Session ${id}`) : `Session ${id}`
  }
  if (nodeId.startsWith('state:')) return nodeId.slice('state:'.length)
  return nodeId
})

function formatNumber(value) {
  return typeof value === 'number' ? value.toFixed(2) : '—'
}

onMounted(() => {
  loadSessions(true, props.projectName)
  statesLoading.value = true
  getProjectStates(props.projectName).then((states) => {
    projectStates.value = states
  }).catch(() => {
    // already surfaced via apiFetch
  }).finally(() => {
    statesLoading.value = false
  })
})

onBeforeUnmount(() => {
  Object.keys(pollTimers).forEach(clearPoll)
})
</script>

<template>
  <div class="project-auto-panel">
    <div class="tests-panel-tree">
      <div class="tests-panel-strategy">
        <label class="tests-panel-strategy-label">
          Strategy
          <select v-model="strategy" class="tests-panel-strategy-select">
            <option value="turn_by_turn">Turn-by-turn</option>
            <option value="batch">Batch</option>
          </select>
        </label>
      </div>
      <p v-if="sessionsLoading || statesLoading" class="tests-panel-tree-status">Loading…</p>
      <TestsTree
        v-else
        :project-name="projectName"
        :sessions="sessions"
        :states="projectStates"
        :statuses="currentStrategyStatuses"
        :selected-node-id="selectedNodeId"
        @select="onSelect"
        @activate="onActivate"
      />
    </div>

    <div class="tests-panel-content">
      <p v-if="!selectedNodeId" class="tests-panel-placeholder">Select a node to see its results.</p>

      <template v-else-if="selectedNodeId.startsWith('session:')">
        <h3 class="tests-panel-title">{{ selectedNodeLabel }}</h3>
        <p v-if="selectedRunLoading" class="tests-panel-placeholder">Loading…</p>
        <p v-else-if="!selectedRun" class="tests-panel-placeholder">No test has been run for this session under this strategy yet.</p>
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
                <th>Metric</th><th>Value</th><th>Mean</th><th>Median</th><th>Std dev</th><th>Samples</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="metric in selectedRun.results" :key="metric.name">
                <td>{{ metric.name }}</td>
                <td>{{ formatNumber(metric.value) }}</td>
                <td>{{ formatNumber(metric.mean) }}</td>
                <td>{{ formatNumber(metric.median) }}</td>
                <td>{{ formatNumber(metric.standard_deviation) }}</td>
                <td>{{ metric.sample_count }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>

      <template v-else-if="selectedNodeId.startsWith('state:')">
        <h3 class="tests-panel-title">{{ selectedNodeLabel }}</h3>
        <p v-if="nodeError[selectedCacheKey]" class="tests-panel-error">{{ nodeError[selectedCacheKey] }}</p>
        <p v-else-if="!nodeLastResult[selectedCacheKey]" class="tests-panel-placeholder">
          No test has been run for this state under this strategy yet.
        </p>
        <table v-else class="tests-panel-metrics-table">
          <thead>
            <tr>
              <th>Metric</th><th>Value</th><th>Mean</th><th>Median</th><th>Std dev</th><th>Samples</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{{ nodeLastResult[selectedCacheKey].name }}</td>
              <td>{{ formatNumber(nodeLastResult[selectedCacheKey].value) }}</td>
              <td>{{ formatNumber(nodeLastResult[selectedCacheKey].mean) }}</td>
              <td>{{ formatNumber(nodeLastResult[selectedCacheKey].median) }}</td>
              <td>{{ formatNumber(nodeLastResult[selectedCacheKey].standard_deviation) }}</td>
              <td>{{ nodeLastResult[selectedCacheKey].sample_count }}</td>
            </tr>
          </tbody>
        </table>
      </template>

      <p v-else class="tests-panel-placeholder">{{ selectedNodeLabel }}</p>
    </div>
  </div>
</template>

<style scoped>
.project-auto-panel {
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
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-right: 1px solid #ddd;
  background: #f9fafb;
}

.tests-panel-strategy {
  flex-shrink: 0;
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid #ddd;
}

.tests-panel-strategy-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.tests-panel-strategy-select {
  font-size: 0.8rem;
  padding: 0.2rem 0.4rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  text-transform: none;
  letter-spacing: normal;
  font-weight: 400;
  color: #222;
}

.tests-panel-tree-status {
  padding: 0.75rem 0.9rem;
  font-size: 0.85rem;
  color: #666;
}

.tests-panel-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 1rem;
}

.tests-panel-title {
  margin: 0 0 0.75rem;
  font-size: 1rem;
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
</style>
