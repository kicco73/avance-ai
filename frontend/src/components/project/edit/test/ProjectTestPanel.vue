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

// Lets EditProjectView.vue mirror this panel's own selection into the
// Inspector's read-only Info tab — this stays the single source of truth
// for selectedNodeId, the parent just gets told when it changes.
const emit = defineEmits(['select'])

// Applies to whichever node gets activated next — one shared control, not a per-node choice.
const strategy = ref('batch')

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

// running beats fail beats warning beats ok; idle only when nothing in
// scope has ever run under this strategy — used to roll leaf statuses
// up into their branch/root node.
function aggregateStatus(statuses) {
  if (statuses.some((s) => s === 'running')) return 'running'
  if (statuses.some((s) => s === 'fail')) return 'fail'
  if (statuses.some((s) => s === 'warning')) return 'warning'
  if (statuses.length > 0 && statuses.every((s) => s === 'ok')) return 'ok'
  return 'idle'
}

// Every state leaf's own status under the active strategy, rolled up
// into states-branch's own status below.
const statesBranchStatus = computed(() => {
  const prefix = `${strategy.value}:state:`
  const statuses = Object.entries(nodeStatuses.value)
    .filter(([key]) => key.startsWith(prefix))
    .map(([, status]) => status)
  return aggregateStatus(statuses)
})

// TestsTree only ever sees the active strategy's own statuses — a node
// with no entry here falls back to its own 'idle' default. states-branch
// and root are derived (not launched jobs in their own right), so they're
// computed here rather than read straight off nodeStatuses.
const currentStrategyStatuses = computed(() => {
  const prefix = `${strategy.value}:`
  const result = {}
  for (const [key, status] of Object.entries(nodeStatuses.value)) {
    if (key.startsWith(prefix)) result[key.slice(prefix.length)] = status
  }
  result['states-branch'] = statesBranchStatus.value
  result['root'] = aggregateStatus([result['sessions-branch'] ?? 'idle', statesBranchStatus.value])
  return result
})

const selectedCacheKey = computed(() => (
  selectedNodeId.value ? cacheKey(strategy.value, selectedNodeId.value) : null
))

// One row per state, its own SignalAccuracy result under the active
// strategy — states-branch's own "aggregate stats" view.
const statesAggregateRows = computed(() => {
  const prefix = `${strategy.value}:state:`
  return Object.entries(nodeLastResult.value)
    .filter(([key, result]) => key.startsWith(prefix) && result)
    .map(([key, result]) => ({ ...result, name: key.slice(prefix.length) }))
})

// Sample-count-weighted average across every state that has a result —
// the single "how's the whole state machine doing" number.
const statesOverall = computed(() => {
  const rows = statesAggregateRows.value
  const totalSamples = rows.reduce((sum, row) => sum + (row.sample_count || 0), 0)
  if (totalSamples === 0) return null
  const weightedSum = rows.reduce((sum, row) => sum + (row.value || 0) * (row.sample_count || 0), 0)
  return { value: weightedSum / totalSamples, sample_count: totalSamples }
})

const statesFailedEntries = computed(() => {
  const prefix = `${strategy.value}:state:`
  return Object.entries(nodeError.value)
    .filter(([key, error]) => key.startsWith(prefix) && error)
    .map(([key, error]) => ({ name: key.slice(prefix.length), error }))
})

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

function pollSessionRun(nodeId, runStrategy, key, runId, mirrorLeafNodeIds = []) {
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
    const outcome = statusFromOutcome(run.status, run.error)
    setStatus(key, outcome)
    // The whole-project replay is one job with no per-session status of
    // its own — every session leaf lit up together at launch mirrors
    // this same run's outcome now that it's done, the closest we can get
    // to "that session's own test" without one job per session.
    mirrorLeafNodeIds.forEach((leafNodeId) => setStatus(cacheKey(runStrategy, leafNodeId), outcome))
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

async function activateSessionLeaf(nodeId, activeStrategy) {
  const key = cacheKey(activeStrategy, nodeId)
  setStatus(key, 'running')
  try {
    const sessionId = Number(nodeId.slice('session:'.length))
    const run = await postBenchmarkRun(props.projectName, sessionId, activeStrategy)
    pollSessionRun(nodeId, activeStrategy, key, run.id)
  } catch {
    // already surfaced via apiFetch
    setStatus(key, 'fail')
  }
}

async function activateStateLeaf(nodeId, activeStrategy) {
  const key = cacheKey(activeStrategy, nodeId)
  setStatus(key, 'running')
  try {
    const stateKey = nodeId.slice('state:'.length)
    const { job_id } = await postStateTest(props.projectName, stateKey, activeStrategy)
    pollStateJob(key, job_id)
  } catch {
    // already surfaced via apiFetch
    setStatus(key, 'fail')
  }
}

// Every session-leaf nodeId the "Sessions" branch shows — same filter as
// TestsTree.vue's own annotatedSessions.
function annotatedSessionNodeIds() {
  return sessions.value.filter((s) => s.has_annotations).map((s) => `session:${s.id}`)
}

// The whole-project replay (session_id: null) — same backend run kind as
// a single session's, just scoped to every labeled session at once (see
// BenchmarkRunService.create_run). Both sessions-branch and root show it.
async function activateWholeProjectRun(activeStrategy) {
  const key = cacheKey(activeStrategy, 'sessions-branch')
  const leafNodeIds = annotatedSessionNodeIds()
  setStatus(key, 'running')
  // Light up every session leaf right away — this one job replays all of
  // them at once, so from the tree's point of view every session under
  // it is "running" for as long as it is.
  leafNodeIds.forEach((leafNodeId) => setStatus(cacheKey(activeStrategy, leafNodeId), 'running'))
  try {
    const run = await postBenchmarkRun(props.projectName, null, activeStrategy)
    pollSessionRun('sessions-branch', activeStrategy, key, run.id, leafNodeIds)
  } catch {
    // already surfaced via apiFetch
    setStatus(key, 'fail')
    leafNodeIds.forEach((leafNodeId) => setStatus(cacheKey(activeStrategy, leafNodeId), 'fail'))
  }
}

async function activateAllStates(activeStrategy) {
  await Promise.all(
    projectStates.value.map((stateKey) => activateStateLeaf(`state:${stateKey}`, activeStrategy))
  )
}

async function onActivate(nodeId) {
  // Pressing play selects the node it belongs to, same as clicking its
  // row — the results panel should already be pointed at it once the
  // run/job(s) finish.
  onSelect(nodeId)
  // Snapshot the strategy at launch time — every job this dispatches is
  // pinned to it regardless of whether the dropdown changes before they finish.
  const activeStrategy = strategy.value
  if (nodeId.startsWith('session:')) {
    await activateSessionLeaf(nodeId, activeStrategy)
  } else if (nodeId.startsWith('state:')) {
    await activateStateLeaf(nodeId, activeStrategy)
  } else if (nodeId === 'sessions-branch') {
    await activateWholeProjectRun(activeStrategy)
  } else if (nodeId === 'states-branch') {
    await activateAllStates(activeStrategy)
  } else if (nodeId === 'root') {
    // Every sub-test at once: the whole-project replay plus every state's own test.
    await Promise.all([activateWholeProjectRun(activeStrategy), activateAllStates(activeStrategy)])
  }
}

async function loadSelectedRun(nodeId) {
  // null for sessions-branch: the whole-project run, not a filter (see
  // backend BenchmarkRunService.list_runs' own session_id docstring).
  const sessionId = nodeId.startsWith('session:') ? Number(nodeId.slice('session:'.length)) : null
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

// root deliberately isn't one of these: sessions-branch and states-branch
// each have a real result (a whole-project run, a weighted state average),
// but there's no sound way to combine those two into one number — root
// shows a plain "go pick a branch" message instead (see the template).
function isRunNode(nodeId) {
  return nodeId.startsWith('session:') || nodeId === 'sessions-branch'
}

async function onSelect(nodeId) {
  selectedNodeId.value = nodeId
  emit('select', nodeId)
  selectedRun.value = null
  if (!isRunNode(nodeId)) return
  await loadSelectedRun(nodeId)
}

// Switching strategy must refresh whatever's on screen for the currently
// selected node — otherwise it would keep showing the other strategy's
// last-fetched run.
watch(strategy, () => {
  if (selectedNodeId.value && isRunNode(selectedNodeId.value)) {
    loadSelectedRun(selectedNodeId.value)
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
  // selectedNodeId always starts null on a fresh mount (this tab isn't
  // kept alive while closed — see EditProjectView.vue's autoOpen v-if),
  // so there's never anything already selected to defer to here.
  onSelect('root')
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
            <option value="batch">Batch</option>
            <option value="turn_by_turn">Turn-by-turn</option>
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

      <template v-else-if="selectedNodeId === 'root'">
        <h3 class="tests-panel-title">{{ selectedNodeLabel }}</h3>
        <p class="tests-panel-placeholder">Aggregates not available at this time.<br />Please select Sessions or States to see results.</p>
      </template>

      <template v-else-if="selectedNodeId.startsWith('session:') || selectedNodeId === 'sessions-branch'">
        <h3 class="tests-panel-title">{{ selectedNodeLabel }}</h3>
        <p v-if="selectedRunLoading" class="tests-panel-placeholder">Loading…</p>
        <p v-else-if="!selectedRun" class="tests-panel-placeholder">
          No test has been run for {{ selectedNodeId.startsWith('session:') ? 'this session' : 'the whole project' }} under this strategy yet.
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

      <template v-else-if="selectedNodeId === 'states-branch'">
        <h3 class="tests-panel-title">{{ selectedNodeLabel }}</h3>
        <div v-if="statesFailedEntries.length" class="tests-panel-error">
          <div v-for="entry in statesFailedEntries" :key="entry.name">{{ entry.name }}: {{ entry.error }}</div>
        </div>
        <p v-if="!statesAggregateRows.length" class="tests-panel-placeholder">
          No state test has been run under this strategy yet.
        </p>
        <table v-else class="tests-panel-metrics-table">
          <thead>
            <tr>
              <th>State</th><th>Value</th><th>Mean</th><th>Median</th><th>Std dev</th><th>Samples</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in statesAggregateRows" :key="row.name">
              <td>{{ row.name }}</td>
              <td>{{ formatNumber(row.value) }}</td>
              <td>{{ formatNumber(row.mean) }}</td>
              <td>{{ formatNumber(row.median) }}</td>
              <td>{{ formatNumber(row.standard_deviation) }}</td>
              <td>{{ row.sample_count }}</td>
            </tr>
            <tr v-if="statesOverall" class="tests-panel-overall-row">
              <td>Overall</td>
              <td>{{ formatNumber(statesOverall.value) }}</td>
              <td colspan="3"></td>
              <td>{{ statesOverall.sample_count }}</td>
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

.tests-panel-overall-row td {
  font-weight: 600;
  border-top: 2px solid #ccc;
}
</style>
