<script setup>
// "Test" mode's content, shown when EditProjectView.vue's `testOpen` is set.
// Two columns: TestsTree on the left (Sessions/States), a node's results on
// the right. Owns all data fetching/launching/polling — TestsTree itself
// (alongside TestNodeButton, both in this same test/ folder) stays purely presentational.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import TestsTree from './TestsTree.vue'
import DocInfoButton from '../../../DocInfoButton.vue'
import MetricDetail from '../../../inspector/MetricDetail.vue'
import ModelMenu from '../../../ModelMenu.vue'
import {
  deleteBenchmarkRuns, getBenchmarkMetrics, getBenchmarkRun, getBenchmarkRuns, getProjectStates, getStateJob,
  postBenchmarkRun, postSessionsRun, postStateTest, postUserSessionsRun, postUsersAggregation
} from '../../../../api.js'
import { loadSessions, sessions, sessionsLoading } from '../../../../chatStore.js'
import { confirmDialog } from '../../../../dialogStore.js'

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

const strategyOpen = ref(false)
const strategyBtnEl = ref(null)
const strategyPanelEl = ref(null)
const strategyPanelStyle = ref({})
const strategyLabel = computed(() => strategyLabels[strategy.value])

async function positionStrategyPanel() {
  await nextTick()
  const btn = strategyBtnEl.value
  if (!btn) return
  const btnRect = btn.getBoundingClientRect()
  strategyPanelStyle.value = { left: `${btnRect.left}px`, top: `${btnRect.bottom + 4}px` }
}

async function toggleStrategyMenu() {
  strategyOpen.value = !strategyOpen.value
  if (strategyOpen.value) await positionStrategyPanel()
}

function closeStrategyMenu() {
  strategyOpen.value = false
}

function selectStrategy(value) {
  strategy.value = value
  closeStrategyMenu()
}

function handleStrategyClickOutside(event) {
  if (!strategyOpen.value) return
  if (strategyBtnEl.value?.contains(event.target)) return
  if (strategyPanelEl.value?.contains(event.target)) return
  closeStrategyMenu()
}
document.addEventListener('click', handleStrategyClickOutside, true)
window.addEventListener('resize', closeStrategyMenu)
window.addEventListener('scroll', closeStrategyMenu, true)

const treeWidth = ref(280)
let draggingTree = false

function startTreeDrag(event) {
  draggingTree = true
  event.preventDefault()
}

function onTreeDrag(event) {
  if (!draggingTree) return
  treeWidth.value = Math.min(480, Math.max(200, treeWidth.value + event.movementX))
}

function stopTreeDrag() {
  draggingTree = false
}

const projectStates = ref([])
const statesLoading = ref(false)

// name -> {ui_label, ui_description} for the fixed core-benchmark-metric
// registry (state_accuracy, signal_accuracy, ...) — every result row's own
// `name` below is one of these, resolved for display instead of the raw
// identifier. Loaded once; this registry is static per backend build, not
// per-project data.
const metricDefinitions = ref({})

async function loadMetricDefinitions() {
  try {
    const metrics = await getBenchmarkMetrics(props.projectName)
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
const nodeProgress = ref({})
// A state node's own most recent job result — Signal Accuracy only, kept
// purely client-side (no server-side history for an ephemeral job, see
// backend jobs/job_sink.py's InMemoryJobSink), lost on page refresh.
const nodeLastResult = ref({})
// A state node's own most recent job failure message, alongside
// nodeLastResult above (null result on failure must stay distinguishable
// from "never run").
const nodeError = ref({})
// sessions-branch/a user node/users-branch's own most recent aggregation
// job result — already the per-metric list, kept client-side only, same
// as nodeLastResult/nodeError above.
const aggregateResults = ref({})
const aggregateErrors = ref({})
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
  result['root'] = aggregateStatus([
    result['sessions-branch'] ?? 'idle', statesBranchStatus.value, result['users-branch'] ?? 'idle'
  ])
  return result
})

const currentStrategyProgress = computed(() => {
  const prefix = `${strategy.value}:`
  const result = {}
  for (const [key, progress] of Object.entries(nodeProgress.value)) {
    if (key.startsWith(prefix)) result[key.slice(prefix.length)] = progress
  }
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

function setProgress(key, current, total) {
  nodeProgress.value = { ...nodeProgress.value, [key]: { current, total } }
}

function clearProgress(key) {
  const next = { ...nodeProgress.value }
  delete next[key]
  nodeProgress.value = next
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
      setProgress(key, run.processed_messages, run.total_messages)
      pollTimers[key] = setTimeout(tick, 1000)
      return
    }
    const outcome = statusFromOutcome(run.status, run.error)
    setStatus(key, outcome)
    if (selectedNodeId.value === nodeId && strategy.value === runStrategy) selectedRun.value = run
  }
  tick()
}

function pollStateJob(key, jobId, runStrategy, mirrorLeafNodeIds = []) {
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
      if (job != null) setProgress(key, job.progress_current, job.progress_total)
      pollTimers[key] = setTimeout(tick, 1000)
      return
    }
    const outcome = statusFromOutcome(job.status, job.error)
    setStatus(key, outcome)
    // A state test replays every session annotated with this state under
    // the hood (see BenchmarkRunService.start_job) — mirroring its outcome
    // onto those session leaves the same way activateSessionsRun does.
    mirrorLeafNodeIds.forEach((leafNodeId) => setStatus(cacheKey(runStrategy, leafNodeId), outcome))
    nodeError.value = { ...nodeError.value, [key]: job.status === 'failed' ? job.error : null }
    nodeLastResult.value = {
      ...nodeLastResult.value,
      [key]: job.result ? JSON.parse(job.result) : null
    }
  }
  tick()
}

function pollAggregationJob(key, jobId, runStrategy, mirrorLeafNodeIds = []) {
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
      if (job != null) setProgress(key, job.progress_current, job.progress_total)
      pollTimers[key] = setTimeout(tick, 1000)
      return
    }
    const outcome = statusFromOutcome(job.status, job.error)
    setStatus(key, outcome)
    mirrorLeafNodeIds.forEach((leafNodeId) => setStatus(cacheKey(runStrategy, leafNodeId), outcome))
    aggregateErrors.value = { ...aggregateErrors.value, [key]: job.status === 'failed' ? job.error : null }
    aggregateResults.value = {
      ...aggregateResults.value,
      [key]: job.result ? JSON.parse(job.result) : null
    }
  }
  tick()
}

async function activateSessionLeaf(nodeId, activeStrategy) {
  const key = cacheKey(activeStrategy, nodeId)
  setStatus(key, 'running')
  clearProgress(key)
  try {
    const sessionId = Number(nodeId.slice('session:'.length))
    const run = await postBenchmarkRun(props.projectName, sessionId, activeStrategy)
    setProgress(key, run.processed_messages, run.total_messages)
    pollSessionRun(nodeId, activeStrategy, key, run.id)
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

async function activateStateLeaf(nodeId, activeStrategy, mirrorLeafNodeIds = []) {
  const key = cacheKey(activeStrategy, nodeId)
  setStatus(key, 'running')
  clearProgress(key)
  // A state test replays every annotated session under the hood (see
  // BenchmarkRunService.start_job) — mirrored here the same way
  // activateSessionsRun mirrors its own whole-project replay.
  mirrorLeafNodeIds.forEach((leafNodeId) => setStatus(cacheKey(activeStrategy, leafNodeId), 'running'))
  try {
    const stateKey = nodeId.slice('state:'.length)
    const { job_id } = await postStateTest(props.projectName, stateKey, activeStrategy)
    pollStateJob(key, job_id, activeStrategy, mirrorLeafNodeIds)
  } catch {
    // already surfaced via apiFetch
    setStatus(key, 'fail')
    mirrorLeafNodeIds.forEach((leafNodeId) => setStatus(cacheKey(activeStrategy, leafNodeId), 'fail'))
  }
}

// The whole-project replay (session_id: null) — same backend run kind as
// a single session's, just scoped to every labeled session at once (see
// BenchmarkRunService.create_run). Both sessions-branch and root show it.
async function activateSessionsRun(activeStrategy) {
  const key = cacheKey(activeStrategy, 'sessions-branch')
  const leafNodeIds = annotatedSessionNodeIds()
  setStatus(key, 'running')
  clearProgress(key)
  leafNodeIds.forEach((leafNodeId) => {
    setStatus(cacheKey(activeStrategy, leafNodeId), 'running')
    clearProgress(cacheKey(activeStrategy, leafNodeId))
  })
  try {
    const { job_id } = await postSessionsRun(props.projectName, activeStrategy)
    pollAggregationJob(key, job_id, activeStrategy, leafNodeIds)
  } catch {
    // already surfaced via apiFetch
    setStatus(key, 'fail')
    leafNodeIds.forEach((leafNodeId) => setStatus(cacheKey(activeStrategy, leafNodeId), 'fail'))
  }
}

async function activateAllStates(activeStrategy) {
  const leafNodeIds = annotatedSessionNodeIds()
  await Promise.all(
    projectStates.value.map((stateKey) => activateStateLeaf(`state:${stateKey}`, activeStrategy, leafNodeIds))
  )
}

// Every user-leaf nodeId the "Users" branch shows — one per distinct
// username among annotated sessions, same source annotatedSessionNodeIds
// uses for its own leaves.
function annotatedUsernames() {
  return [...new Set(sessions.value.filter((s) => s.has_annotations).map((s) => s.username))]
}

function annotatedSessionNodeIdsFor(username) {
  return sessions.value.filter((s) => s.has_annotations && s.username === username).map((s) => `session:${s.id}`)
}

async function activateUserLeaf(nodeId, activeStrategy) {
  const key = cacheKey(activeStrategy, nodeId)
  const username = nodeId.slice('user:'.length)
  const leafNodeIds = annotatedSessionNodeIdsFor(username)
  setStatus(key, 'running')
  clearProgress(key)
  leafNodeIds.forEach((leafNodeId) => {
    setStatus(cacheKey(activeStrategy, leafNodeId), 'running')
    clearProgress(cacheKey(activeStrategy, leafNodeId))
  })
  try {
    const { job_id } = await postUserSessionsRun(props.projectName, username, activeStrategy)
    pollAggregationJob(key, job_id, activeStrategy, leafNodeIds)
  } catch {
    // already surfaced via apiFetch
    setStatus(key, 'fail')
    leafNodeIds.forEach((leafNodeId) => setStatus(cacheKey(activeStrategy, leafNodeId), 'fail'))
  }
}

// The "Users" branch's own root aggregation — a mean across one
// whole-project-scope run per distinct annotated user (see backend
// BenchmarkRunService.start_users_aggregation_job).
async function activateUsersAggregation(activeStrategy) {
  const key = cacheKey(activeStrategy, 'users-branch')
  const userLeafNodeIds = annotatedUsernames().map((username) => `user:${username}`)
  const sessionLeafNodeIds = annotatedSessionNodeIds()
  setStatus(key, 'running')
  clearProgress(key)
  userLeafNodeIds.forEach((leafNodeId) => setStatus(cacheKey(activeStrategy, leafNodeId), 'running'))
  sessionLeafNodeIds.forEach((leafNodeId) => setStatus(cacheKey(activeStrategy, leafNodeId), 'running'))
  try {
    const { job_id } = await postUsersAggregation(props.projectName, activeStrategy)
    pollAggregationJob(key, job_id, activeStrategy, [...userLeafNodeIds, ...sessionLeafNodeIds])
  } catch {
    // already surfaced via apiFetch
    setStatus(key, 'fail')
    ;[...userLeafNodeIds, ...sessionLeafNodeIds].forEach((leafNodeId) => setStatus(cacheKey(activeStrategy, leafNodeId), 'fail'))
  }
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
    await activateStateLeaf(nodeId, activeStrategy, annotatedSessionNodeIds())
  } else if (nodeId.startsWith('user:')) {
    await activateUserLeaf(nodeId, activeStrategy)
  } else if (nodeId === 'sessions-branch') {
    await activateSessionsRun(activeStrategy)
  } else if (nodeId === 'states-branch') {
    await activateAllStates(activeStrategy)
  } else if (nodeId === 'users-branch') {
    await activateUsersAggregation(activeStrategy)
  } else if (nodeId === 'root') {
    // Every sub-test at once: the whole-project replay, every state's own
    // test, and the users aggregation.
    await Promise.all([
      activateSessionsRun(activeStrategy), activateAllStates(activeStrategy), activateUsersAggregation(activeStrategy)
    ])
  }
}

async function loadSelectedRun(nodeId) {
  const sessionId = Number(nodeId.slice('session:'.length))
  selectedRunLoading.value = true
  try {
    const runs = await getBenchmarkRuns(props.projectName, sessionId)
    // Already most-recent-first (see backend BenchmarkRunService.list_runs)
    // — filtered to the active strategy, since turn_by_turn and batch
    // runs aren't comparable and must never be shown as if they were.
    const run = runs.find((run) => run.strategy === strategy.value) ?? null
    selectedRun.value = run
    if (run != null && run.status !== 'pending' && run.status !== 'running') {
      setStatus(cacheKey(strategy.value, nodeId), statusFromOutcome(run.status, run.error))
    }
  } catch {
    selectedRun.value = null
  } finally {
    selectedRunLoading.value = false
  }
}

function isRunNode(nodeId) {
  return nodeId.startsWith('session:')
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
  if (nodeId === 'users-branch') return 'Users'
  if (nodeId.startsWith('session:')) {
    const id = Number(nodeId.slice('session:'.length))
    const session = sessions.value.find((s) => s.id === id)
    return session ? (session.title || session.end_state || `Session ${id}`) : `Session ${id}`
  }
  if (nodeId.startsWith('state:')) return nodeId.slice('state:'.length)
  if (nodeId.startsWith('user:')) return nodeId.slice('user:'.length)
  return nodeId
})

function formatNumber(value) {
  return typeof value === 'number' ? value.toFixed(2) : '—'
}

const resettingCache = ref(false)

const anyTestExecuted = computed(() => Object.values(nodeStatuses.value).some((status) => status !== 'idle'))

async function onResetCache() {
  if (strategy.value === 'turn_by_turn') {
    const ok = await confirmDialog({
      title: 'Reset test cache',
      body: 'Turn-by-turn tests replay one AI call per message — resetting the cache forces every test to run again from scratch, which can be expensive. Continue?',
      okLabel: 'Reset',
      danger: true
    })
    if (!ok) return
  }
  resettingCache.value = true
  try {
    await deleteBenchmarkRuns(props.projectName)
    Object.keys(pollTimers).forEach(clearPoll)
    nodeStatuses.value = {}
    nodeLastResult.value = {}
    nodeError.value = {}
    aggregateResults.value = {}
    aggregateErrors.value = {}
    selectedRun.value = null
    if (selectedNodeId.value && isRunNode(selectedNodeId.value)) {
      await loadSelectedRun(selectedNodeId.value)
    }
  } catch {
    // already surfaced via apiFetch
  } finally {
    resettingCache.value = false
  }
}

onMounted(() => {
  // selectedNodeId always starts null on a fresh mount (this tab isn't
  // kept alive while closed — see EditProjectView.vue's autoOpen v-if),
  // so there's never anything already selected to defer to here.
  onSelect('root')
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
  window.addEventListener('mousemove', onTreeDrag)
  window.addEventListener('mouseup', stopTreeDrag)
})

onBeforeUnmount(() => {
  Object.keys(pollTimers).forEach(clearPoll)
  window.removeEventListener('mousemove', onTreeDrag)
  window.removeEventListener('mouseup', stopTreeDrag)
  document.removeEventListener('click', handleStrategyClickOutside, true)
  window.removeEventListener('resize', closeStrategyMenu)
  window.removeEventListener('scroll', closeStrategyMenu, true)
})
</script>

<template>
  <div class="project-test-panel">
    <div class="tests-panel-tree" :style="{ width: treeWidth + 'px' }">
      <div class="tests-panel-strategy">
        <span class="tests-panel-tree-header">Test explorer</span>
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
      <p v-if="sessionsLoading || statesLoading" class="tests-panel-tree-status">Loading…</p>
      <TestsTree
        v-else
        :project-name="projectName"
        :sessions="sessions"
        :states="projectStates"
        :statuses="currentStrategyStatuses"
        :progresses="currentStrategyProgress"
        :selected-node-id="selectedNodeId"
        @select="onSelect"
        @activate="onActivate"
      />
    </div>

    <div class="tests-panel-split-divider" @mousedown="startTreeDrag"></div>

    <div class="tests-panel-main">
      <div class="tests-panel-toolbar">
        <div v-if="selectedNodeId" class="tests-panel-toolbar-title">
          <h3 class="tests-panel-title">{{ selectedNodeLabel }}</h3>
          <DocInfoButton doc-name="benchmark" title="Benchmark" />
        </div>
        <div v-else class="tests-panel-toolbar-title"></div>
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
          <ModelMenu />
        </div>
      </div>
      <div class="tests-panel-content">
      <p v-if="!selectedNodeId" class="tests-panel-placeholder">Select a node to see its results.</p>

      <template v-else-if="selectedNodeId === 'root'">
        <p class="tests-panel-placeholder">Aggregates not available at this time.<br />Please select Sessions or States to see results.</p>
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
                <th>Metric</th><th>Mean</th><th>Median</th><th>Std dev</th><th>Samples</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="metric in selectedRun.results" :key="metric.name" :class="{ 'tests-panel-row-empty': !metric.sample_count }">
                <td><MetricDetail :label="metricLabel(metric.name)" :description="metricDescription(metric.name)" :value="metric.value" /></td>
                <td>{{ formatNumber(metric.mean) }}</td>
                <td>{{ formatNumber(metric.median) }}</td>
                <td>{{ formatNumber(metric.standard_deviation) }}</td>
                <td>{{ metric.sample_count }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>

      <template v-else-if="selectedNodeId === 'sessions-branch' || selectedNodeId.startsWith('user:') || selectedNodeId === 'users-branch'">
        <p v-if="aggregateErrors[selectedCacheKey]" class="tests-panel-error">{{ aggregateErrors[selectedCacheKey] }}</p>
        <p v-else-if="!aggregateResults[selectedCacheKey] || !aggregateResults[selectedCacheKey].length" class="tests-panel-placeholder">
          No test has been run for {{ selectedNodeId.startsWith('user:') ? 'this user' : selectedNodeId === 'users-branch' ? 'the users aggregation' : 'the whole project' }} under this strategy yet.
        </p>
        <table v-else class="tests-panel-metrics-table">
          <thead>
            <tr>
              <th>Metric</th><th>Mean</th><th>Median</th><th>Std dev</th><th>Samples</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="metric in aggregateResults[selectedCacheKey]" :key="metric.name"
              :class="{ 'tests-panel-row-empty': !metric.sample_count }"
            >
              <td><MetricDetail :label="metricLabel(metric.name)" :description="metricDescription(metric.name)" :value="metric.value" /></td>
              <td>{{ formatNumber(metric.mean) }}</td>
              <td>{{ formatNumber(metric.median) }}</td>
              <td>{{ formatNumber(metric.standard_deviation) }}</td>
              <td>{{ metric.sample_count }}</td>
            </tr>
          </tbody>
        </table>
      </template>

      <template v-else-if="selectedNodeId.startsWith('state:')">
        <p v-if="nodeError[selectedCacheKey]" class="tests-panel-error">{{ nodeError[selectedCacheKey] }}</p>
        <p v-else-if="!nodeLastResult[selectedCacheKey]" class="tests-panel-placeholder">
          No test has been run for this state under this strategy yet.
        </p>
        <table v-else class="tests-panel-metrics-table">
          <thead>
            <tr>
              <th>Metric</th><th>Mean</th><th>Median</th><th>Std dev</th><th>Samples</th>
            </tr>
          </thead>
          <tbody>
            <tr :class="{ 'tests-panel-row-empty': !nodeLastResult[selectedCacheKey].sample_count }">
              <td><MetricDetail :label="metricLabel(nodeLastResult[selectedCacheKey].name)" :description="metricDescription(nodeLastResult[selectedCacheKey].name)" :value="nodeLastResult[selectedCacheKey].value" /></td>
              <td>{{ formatNumber(nodeLastResult[selectedCacheKey].mean) }}</td>
              <td>{{ formatNumber(nodeLastResult[selectedCacheKey].median) }}</td>
              <td>{{ formatNumber(nodeLastResult[selectedCacheKey].standard_deviation) }}</td>
              <td>{{ nodeLastResult[selectedCacheKey].sample_count }}</td>
            </tr>
          </tbody>
        </table>
      </template>

      <template v-else-if="selectedNodeId === 'states-branch'">
        <div v-if="statesFailedEntries.length" class="tests-panel-error">
          <div v-for="entry in statesFailedEntries" :key="entry.name">{{ entry.name }}: {{ entry.error }}</div>
        </div>
        <p v-if="!statesAggregateRows.length" class="tests-panel-placeholder">
          No state test has been run under this strategy yet.
        </p>
        <table v-else class="tests-panel-metrics-table">
          <thead>
            <tr>
              <th>State</th><th>Mean</th><th>Median</th><th>Std dev</th><th>Samples</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in statesAggregateRows" :key="row.name" :class="{ 'tests-panel-row-empty': !row.sample_count }">
              <td><MetricDetail :label="row.name" :value="row.value" /></td>
              <td>{{ formatNumber(row.mean) }}</td>
              <td>{{ formatNumber(row.median) }}</td>
              <td>{{ formatNumber(row.standard_deviation) }}</td>
              <td>{{ row.sample_count }}</td>
            </tr>
            <tr v-if="statesOverall" class="tests-panel-overall-row" :class="{ 'tests-panel-row-empty': !statesOverall.sample_count }">
              <td><MetricDetail label="Overall" :value="statesOverall.value" /></td>
              <td colspan="3"></td>
              <td>{{ statesOverall.sample_count }}</td>
            </tr>
          </tbody>
        </table>
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

.tests-panel-reset-btn {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  padding: 0;
}

.tests-panel-reset-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.tests-panel-reset-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
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

.tests-panel-title {
  margin: 0;
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

.tests-panel-row-empty td {
  color: #aaa;
}
</style>
